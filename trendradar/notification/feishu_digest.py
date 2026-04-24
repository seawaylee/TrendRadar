# coding=utf-8
"""
飞书摘要卡片

为财经场景提供单条摘要推送：
- 热榜 + RSS 合并挑选
- 去重后最多保留 top N
- 记录已发送历史，避免晨报/晚报重复
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
from html import unescape
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

from trendradar.utils.time import DEFAULT_TIMEZONE, format_iso_time_friendly


DEFAULT_HISTORY_FILE = "output/meta/feishu_digest_history.json"
INVESCO_QQQ_ABOUT_URL = "https://www.invesco.com/content/invesco/qqq-etf/en/about.html"
SSGA_SPY_URL = "https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-500-etf-trust-spy"
EM_CLIST_URL = "https://69.push2.eastmoney.com/api/qt/clist/get"
EM_CLIST_UT = "bd1d9ddb04089700cf9c27f6f7426281"
US_CHINESE_BOARD_FS = "b:MK0201"
US_FAMOUS_BOARD_FS = "b:MK0001"
_FOCUS_CONSTITUENT_CACHE: Dict[str, Dict[str, Any]] = {}

OFFICIAL_SOURCE_KEYWORDS = (
    "美联储",
    "SEC",
    "港交所",
    "欧洲央行",
    "英国央行",
    "BIS",
    "经济分析局",
    "劳工统计局",
)

PREMIUM_SOURCE_KEYWORDS = (
    "财联社",
    "华尔街见闻",
    "雅虎财经",
    "Yahoo",
    "澎湃",
)

SOCIAL_SOURCE_KEYWORDS = (
    "微博",
    "知乎",
    "百度",
    "bilibili",
    "今日头条",
)

GROUP_WEIGHTS = {
    "事件触发": 48,
    "宏观政策": 56,
    "通胀就业": 52,
    "汇率债券": 50,
    "大宗商品": 50,
    "中港股市": 48,
    "美股与波动率": 48,
    "地缘与贸易": 46,
    "资金线索": 38,
    "核心公司": 36,
    "财报": 34,
    "监管": 34,
    "指数与情绪": 26,
    "板块异动": 22,
}

TITLE_TRIGGER_WORDS = (
    "降息",
    "加息",
    "利率",
    "cpi",
    "ppi",
    "非农",
    "财报",
    "业绩",
    "回购",
    "减持",
    "增持",
    "停牌",
    "复牌",
    "处罚",
    "立案",
    "问询",
    "违约",
    "暴雷",
    "制裁",
    "关税",
    "停火",
    "谈判",
    "出口管制",
    "ipo",
    "订单",
)

MARKET_PRIORITY_KEYWORDS = (
    "a股",
    "港股",
    "美股",
    "沪指",
    "恒生",
    "恒生科技",
    "纳指",
    "标普",
    "道指",
    "美联储",
    "央行",
    "利率",
    "美债",
    "收益率",
    "美元",
    "人民币",
    "原油",
    "黄金",
    "白银",
    "铜",
    "vix",
    "关税",
    "停火",
    "出口管制",
)

LOW_SIGNAL_PATTERNS = (
    "earnings preview",
    "what to expect",
    "buy, sell, or hold",
    "should you buy",
    "worth watching",
    "call summary",
    "q1 2026 earnings call summary",
    "ahead of earnings",
    "past 5 days",
    "trades at just",
    "after losing",
)

DEFAULT_SCORING_CONFIG: Dict[str, Any] = {
    "minimum_score": 0,
    "adaptive_min_score": {
        "enabled": False,
        "when_empty": True,
        "target_items": 5,
        "floor": 0,
    },
    "source_keywords": {
        "official": list(OFFICIAL_SOURCE_KEYWORDS),
        "premium": list(PREMIUM_SOURCE_KEYWORDS),
        "social": list(SOCIAL_SOURCE_KEYWORDS),
    },
    "source_weights": {
        "official": 120,
        "premium": 80,
        "social": 24,
        "default": 40,
    },
    "group_weights": dict(GROUP_WEIGHTS),
    "default_group_weight": 18,
    "title_trigger_words": list(TITLE_TRIGGER_WORDS),
    "title_trigger_bonus_per_match": 8,
    "market_priority_keywords": list(MARKET_PRIORITY_KEYWORDS),
    "market_priority_bonus_per_match": 14,
    "market_priority_source_bonus": {
        "财联社": 12,
        "华尔街见闻": 12,
    },
    "low_signal_patterns": list(LOW_SIGNAL_PATTERNS),
    "low_signal_penalty_per_match": 90,
    "rank_bonus_base": 24,
    "section_count_bonus_cap": 10,
    "section_count_bonus_per_item": 4,
    "title_count_bonus_cap": 5,
    "title_count_bonus_per_item": 6,
    "section_percentage_bonus_multiplier": 20,
    "is_new_bonus": 30,
    "rss_bonus": 20,
    "focus_constituents": {
        "enabled": False,
        "top_k": 10,
        "bonus_per_match": 48,
        "cache_hours": 12,
        "sources": [
            {"type": "invesco_qqq"},
            {"type": "ssga_spy_index"},
        ],
        "non_focus_penalty": 42,
        "non_focus_sources": ["雅虎财经", "Yahoo"],
        "non_focus_exempt_groups": [
            "宏观政策",
            "通胀就业",
            "汇率债券",
            "大宗商品",
            "地缘与贸易",
            "指数与情绪",
            "美股与波动率",
            "中港股市",
        ],
    },
}


def _deep_merge_config(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge_config(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def resolve_scoring_config(scoring_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """基于默认值解析评分配置，允许调用方按需覆写。"""
    if not isinstance(scoring_config, dict):
        return deepcopy(DEFAULT_SCORING_CONFIG)
    return _deep_merge_config(DEFAULT_SCORING_CONFIG, scoring_config)


def _config_int(scoring_config: Dict[str, Any], key: str, default: int) -> int:
    try:
        return int(scoring_config.get(key, default))
    except (TypeError, ValueError):
        return default


def _config_strings(
    scoring_config: Dict[str, Any],
    key: str,
    default: Iterable[str],
) -> List[str]:
    value = scoring_config.get(key, default)
    if not isinstance(value, (list, tuple)):
        return [str(item).strip() for item in default if str(item).strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _dict_int(data: Dict[str, Any], key: str, default: int) -> int:
    try:
        return int((data or {}).get(key, default))
    except (TypeError, ValueError, AttributeError):
        return default


def _normalize_match_text(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", str(value or "").lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _clean_company_name_for_alias(name: str) -> str:
    cleaned = _normalize_match_text(name)
    cleaned = re.sub(
        r"\b(inc|corp|corporation|company|co|plc|holdings|holding|group|limited|ltd|class|cl|common|cmn|ads|adr|a|b|c)\b",
        " ",
        cleaned,
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def _build_constituent_aliases(name: str, symbol: str = "") -> List[str]:
    aliases: List[str] = []
    for candidate in (name, _clean_company_name_for_alias(name)):
        normalized = _normalize_match_text(candidate)
        if normalized and normalized not in aliases:
            aliases.append(normalized)

    normalized_symbol = _normalize_match_text(symbol)
    if normalized_symbol and len(normalized_symbol) >= 4 and normalized_symbol not in aliases:
        aliases.append(normalized_symbol)
    return aliases


def _extract_attr(html_text: str, attr_name: str) -> str:
    pattern = rf'{re.escape(attr_name)}="([^"]+)"'
    match = re.search(pattern, html_text or "", re.IGNORECASE)
    if not match:
        return ""
    return unescape(match.group(1)).strip()


def _run_json_request(url: str, *, headers: Optional[Dict[str, str]] = None, timeout: int = 15) -> Optional[Dict[str, Any]]:
    try:
        response = requests.get(url, headers=headers or {}, timeout=timeout)
        if response.status_code != 200:
            return None
        payload = response.json()
        if isinstance(payload, dict):
            return payload
    except Exception:
        return None
    return None


def _build_browser_like_json_headers(referer: str, origin: str) -> Dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
        "Origin": origin,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        'sec-ch-ua': '"Chromium";v="135", "Not-A.Brand";v="8"',
        "sec-ch-ua-mobile": "?0",
        'sec-ch-ua-platform': '"macOS"',
    }


def _fetch_invesco_qqq_constituents(top_k: int) -> List[Dict[str, Any]]:
    try:
        page_response = requests.get(
            INVESCO_QQQ_ABOUT_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        if page_response.status_code != 200:
            return []
        holdings_api = _extract_attr(page_response.text, "data-holdings-api")
        if not holdings_api:
            return []

        payload = _run_json_request(
            holdings_api,
            headers=_build_browser_like_json_headers(
                referer=INVESCO_QQQ_ABOUT_URL,
                origin="https://www.invesco.com",
            ),
        )
        if not payload:
            return []

        items: List[Dict[str, Any]] = []
        for holding in (payload.get("holdings") or [])[: max(1, top_k)]:
            name = str(holding.get("issuerName", "") or "").strip()
            symbol = str(holding.get("ticker", "") or holding.get("symbol", "") or "").strip()
            if not name:
                continue
            try:
                weight = float(holding.get("percentageOfTotalNetAssets", 0) or 0)
            except (TypeError, ValueError):
                weight = 0.0
            items.append(
                {
                    "name": name,
                    "weight": weight,
                    "index": "QQQ",
                    "aliases": _build_constituent_aliases(name, symbol),
                }
            )
        return items
    except Exception:
        return []


def _fetch_invesco_qqq_constituents_via_dom(top_k: int) -> List[Dict[str, Any]]:
    npx_bin = shutil.which("npx") or ""
    if not npx_bin:
        return []
    session_name = f"trendradar-qqq-{int(datetime.now(timezone.utc).timestamp())}"
    cli_base = [npx_bin, "--yes", "--package", "@playwright/cli", "playwright-cli", "--session", session_name]
    try:
        open_proc = subprocess.run(
            cli_base + ["open", INVESCO_QQQ_ABOUT_URL],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if open_proc.returncode != 0:
            return []

        snapshot_proc = subprocess.run(
            cli_base + ["snapshot", "--raw"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception:
        return []
    finally:
        try:
            subprocess.run(
                cli_base + ["close"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except Exception:
            pass

    if snapshot_proc.returncode != 0:
        return []

    raw_lines = snapshot_proc.stdout.splitlines()
    start_idx = next((idx for idx, line in enumerate(raw_lines) if "Top 10 holdings" in line), -1)
    if start_idx < 0:
        return []

    items: List[Dict[str, Any]] = []
    current_name = ""
    table_started = False
    for line in raw_lines[start_idx:]:
        text = re.sub(r"^\s*-\s*", "", line).strip()
        if not text:
            continue
        if "Top 10 Holdings weight" in text or "Fund holdings are subject" in text:
            break
        name_match = re.search(r"generic \[ref=.*?\]:\s*(.+)$", text)
        if not name_match:
            continue
        value = name_match.group(1).strip()
        if value == "Allocation":
            table_started = True
            continue
        if not table_started:
            continue
        if not current_name:
            if "%" not in value and value != "Holdings":
                current_name = value
            continue
        percent_match = re.search(r"(\d+(?:\.\d+)?)%", value)
        if not percent_match:
            continue
        try:
            weight = float(percent_match.group(1))
        except ValueError:
            weight = 0.0
        items.append(
            {
                "name": current_name,
                "weight": weight,
                "index": "QQQ",
                "aliases": _build_constituent_aliases(current_name),
            }
        )
        current_name = ""
        if len(items) >= max(1, top_k):
            break
    return items


def _fetch_ssga_spy_constituents(top_k: int) -> List[Dict[str, Any]]:
    try:
        response = requests.get(SSGA_SPY_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if response.status_code != 200:
            return []
        match = re.search(
            r"<h3>Index Top Holdings <span class=\"date\">as of ([^<]+)</span></h3>(.*?)</table>",
            response.text,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return []

        rows = re.findall(
            r"<tr>\s*<td class=\"label\"[^>]*>([^<]+)</td>.*?<td class=\"data\"[^>]*>([\d.]+)%</td>\s*</tr>",
            match.group(2),
            re.IGNORECASE | re.DOTALL,
        )
        items: List[Dict[str, Any]] = []
        for name, weight_text in rows[: max(1, top_k)]:
            clean_name = unescape(name).strip()
            if not clean_name:
                continue
            try:
                weight = float(weight_text)
            except ValueError:
                weight = 0.0
            items.append(
                {
                    "name": clean_name,
                    "weight": weight,
                    "index": "SPY",
                    "aliases": _build_constituent_aliases(clean_name),
                }
            )
        return items
    except Exception:
        return []


def _fetch_eastmoney_board_snapshot(fs: str, *, max_pages: int = 10, page_size: int = 200) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for page_no in range(1, max(1, max_pages) + 1):
        params = {
            "pn": str(page_no),
            "pz": str(max(1, page_size)),
            "po": "1",
            "np": "1",
            "ut": EM_CLIST_UT,
            "fltt": "2",
            "invt": "2",
            "fid": "f20",
            "fs": fs,
            "fields": "f2,f3,f12,f13,f14,f20",
        }
        try:
            response = requests.get(
                EM_CLIST_URL,
                params=params,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://quote.eastmoney.com/",
                },
                timeout=15,
            )
            if response.status_code != 200:
                break
            payload = response.json()
        except Exception:
            break

        data = payload.get("data") or {}
        diff = data.get("diff")
        if isinstance(diff, dict):
            items = list(diff.values())
        elif isinstance(diff, list):
            items = diff
        else:
            items = []
        if not items:
            break

        rows.extend(item for item in items if isinstance(item, dict))

        total = data.get("total")
        if isinstance(total, int) and len(rows) >= total:
            break
    return rows


def _fetch_eastmoney_us_china_board_constituents(
    top_k: int,
    *,
    min_market_cap_usd: float = 5_000_000_000,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for row in _fetch_eastmoney_board_snapshot(US_CHINESE_BOARD_FS):
        name = str(row.get("f14", "") or "").strip()
        symbol = str(row.get("f12", "") or "").strip().upper()
        if not name:
            continue
        try:
            market_cap = float(row.get("f20", 0) or 0)
        except (TypeError, ValueError):
            market_cap = 0.0
        if market_cap < float(min_market_cap_usd):
            continue
        items.append(
            {
                "name": name,
                "weight": market_cap,
                "index": "US_CHINA_BOARD",
                "aliases": _build_constituent_aliases(name, symbol),
            }
        )

    items.sort(key=lambda item: float(item.get("weight", 0) or 0), reverse=True)
    return items[: max(1, top_k)]


def _fetch_eastmoney_us_famous_board_constituents(
    top_k: int,
    *,
    min_market_cap_usd: float = 0,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for row in _fetch_eastmoney_board_snapshot(US_FAMOUS_BOARD_FS):
        name = str(row.get("f14", "") or "").strip()
        symbol = str(row.get("f12", "") or "").strip().upper()
        if not name:
            continue
        try:
            market_cap = float(row.get("f20", 0) or 0)
        except (TypeError, ValueError):
            market_cap = 0.0
        if market_cap < float(min_market_cap_usd):
            continue
        items.append(
            {
                "name": name,
                "weight": market_cap,
                "index": "US_FAMOUS_BOARD",
                "aliases": _build_constituent_aliases(name, symbol),
            }
        )

    items.sort(key=lambda item: float(item.get("weight", 0) or 0), reverse=True)
    return items[: max(1, top_k)]


def _apply_focus_source_metadata(
    items: List[Dict[str, Any]],
    *,
    source_type: str,
    focus_bonus: int,
) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for item in items:
        local = dict(item)
        local["focus_bonus"] = int(focus_bonus)
        local["focus_source_type"] = source_type
        enriched.append(local)
    return enriched


def _load_focus_constituents(scoring_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    focus_config = scoring_config.get("focus_constituents", {})
    if not isinstance(focus_config, dict) or not focus_config.get("enabled", False):
        return []

    cache_key = json.dumps(focus_config, ensure_ascii=False, sort_keys=True)
    cache_hours = max(1, _dict_int(focus_config, "cache_hours", 12))
    cache_record = _FOCUS_CONSTITUENT_CACHE.get(cache_key)
    if cache_record:
        fetched_at = cache_record.get("fetched_at")
        if isinstance(fetched_at, datetime) and datetime.now(timezone.utc) - fetched_at < timedelta(hours=cache_hours):
            return deepcopy(cache_record.get("items", []))

    top_k = max(1, _dict_int(focus_config, "top_k", 10))
    combined: List[Dict[str, Any]] = []
    sources = focus_config.get("sources", [])
    if not isinstance(sources, (list, tuple)):
        sources = []

    for source in sources:
        if not isinstance(source, dict):
            continue
        source_type = str(source.get("type", "") or "").strip().lower()
        source_top_k = max(1, _dict_int(source, "top_k", top_k))
        source_bonus = _dict_int(source, "bonus_per_match", _dict_int(focus_config, "bonus_per_match", 48))
        if source_type == "invesco_qqq":
            items = _fetch_invesco_qqq_constituents(source_top_k) or _fetch_invesco_qqq_constituents_via_dom(source_top_k)
            combined.extend(_apply_focus_source_metadata(items, source_type=source_type, focus_bonus=source_bonus))
        elif source_type == "ssga_spy_index":
            items = _fetch_ssga_spy_constituents(source_top_k)
            combined.extend(_apply_focus_source_metadata(items, source_type=source_type, focus_bonus=source_bonus))
        elif source_type == "eastmoney_us_china_board":
            items = _fetch_eastmoney_us_china_board_constituents(
                source_top_k,
                min_market_cap_usd=float(source.get("min_market_cap_usd", 5_000_000_000) or 5_000_000_000),
            )
            combined.extend(_apply_focus_source_metadata(items, source_type=source_type, focus_bonus=source_bonus))
        elif source_type == "eastmoney_us_famous_board":
            items = _fetch_eastmoney_us_famous_board_constituents(
                source_top_k,
                min_market_cap_usd=float(source.get("min_market_cap_usd", 0) or 0),
            )
            combined.extend(_apply_focus_source_metadata(items, source_type=source_type, focus_bonus=source_bonus))

    deduped: Dict[str, Dict[str, Any]] = {}
    for item in combined:
        key = _normalize_match_text(item.get("name", ""))
        if not key:
            continue
        existing = deduped.get(key)
        item_bonus = _dict_int(item, "focus_bonus", _dict_int(focus_config, "bonus_per_match", 48))
        existing_bonus = _dict_int(existing or {}, "focus_bonus", _dict_int(focus_config, "bonus_per_match", 48))
        item_weight = float(item.get("weight", 0) or 0)
        existing_weight = float((existing or {}).get("weight", 0) or 0)
        if (
            not existing
            or item_bonus > existing_bonus
            or (item_bonus == existing_bonus and item_weight > existing_weight)
        ):
            deduped[key] = item

    items = sorted(
        deduped.values(),
        key=lambda item: (
            -_dict_int(item, "focus_bonus", _dict_int(focus_config, "bonus_per_match", 48)),
            -float(item.get("weight", 0) or 0),
            item.get("name", ""),
        ),
    )[:top_k * max(1, len(sources))]
    _FOCUS_CONSTITUENT_CACHE[cache_key] = {"fetched_at": datetime.now(timezone.utc), "items": deepcopy(items)}
    return items


def normalize_title(title: str) -> str:
    """归一化标题，用于去重指纹。"""
    cleaned = (title or "").strip().lower()
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]", "", cleaned)
    return cleaned


def _fingerprint(title: str, url: str = "") -> str:
    raw = (url or "").strip() or normalize_title(title)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return digest[:16]


def build_history_path_for_webhook(history_path: Path | str, webhook_url: str) -> Path:
    """为不同 webhook 生成独立的去重历史文件。"""
    return build_history_path_for_route(history_path, webhook_url)


def build_history_path_for_route(history_path: Path | str, route_key: str) -> Path:
    """为不同投递路由生成独立的去重历史文件。"""
    base = Path(history_path)
    suffix = hashlib.sha1((route_key or "").encode("utf-8")).hexdigest()[:8]
    stem = f"{base.stem}-{suffix}" if suffix else base.stem
    return base.with_name(f"{stem}{base.suffix}")


def _resolve_openclaw_bin(explicit_bin: str = "") -> str:
    explicit = str(explicit_bin or "").strip()
    if explicit:
        return explicit

    which_path = shutil.which("openclaw") or ""
    if which_path:
        return which_path

    for candidate in (Path.home() / ".volta/bin/openclaw", Path.home() / ".local/bin/openclaw"):
        if candidate.exists():
            return str(candidate)
    return ""


def _resolve_recent_openclaw_target(account: str) -> str:
    account_id = str(account or "").strip()
    if not account_id:
        return ""

    sessions_file = Path.home() / ".openclaw" / "agents" / account_id / "sessions" / "sessions.json"
    if not sessions_file.exists() or not sessions_file.is_file():
        return ""

    try:
        payload = json.loads(sessions_file.read_text(encoding="utf-8"))
    except Exception:
        return ""

    if not isinstance(payload, dict):
        return ""

    best_target = ""
    best_updated_at = -1
    for value in payload.values():
        if not isinstance(value, dict):
            continue
        last_channel = str(value.get("lastChannel", "") or "").strip().lower()
        if last_channel and last_channel != "feishu":
            continue
        last_account = str(value.get("lastAccountId", "") or "").strip()
        if last_account and last_account != account_id:
            continue

        raw_target = str(value.get("lastTo") or "").strip()
        if not raw_target:
            delivery_context = value.get("deliveryContext") or {}
            if isinstance(delivery_context, dict):
                raw_target = str(delivery_context.get("to") or "").strip()
        if not raw_target:
            continue

        try:
            updated_at = int(value.get("updatedAt", 0) or 0)
        except Exception:
            updated_at = 0
        if updated_at >= best_updated_at:
            best_updated_at = updated_at
            best_target = raw_target
    return best_target


def _source_weight(source_name: str, scoring_config: Dict[str, Any]) -> int:
    name = source_name or ""
    source_keywords = scoring_config.get("source_keywords", {})
    source_weights = scoring_config.get("source_weights", {})

    if any(keyword in name for keyword in source_keywords.get("official", OFFICIAL_SOURCE_KEYWORDS)):
        return int(source_weights.get("official", 120) or 120)
    if any(keyword in name for keyword in source_keywords.get("premium", PREMIUM_SOURCE_KEYWORDS)):
        return int(source_weights.get("premium", 80) or 80)
    if any(keyword in name for keyword in source_keywords.get("social", SOCIAL_SOURCE_KEYWORDS)):
        return int(source_weights.get("social", 24) or 24)
    return int(source_weights.get("default", 40) or 40)


def _group_weight(group_name: str, scoring_config: Dict[str, Any]) -> int:
    group_weights = scoring_config.get("group_weights", {})
    for key, weight in group_weights.items():
        if key in (group_name or ""):
            try:
                return int(weight)
            except (TypeError, ValueError):
                continue
    return _config_int(scoring_config, "default_group_weight", 18)


def _title_trigger_bonus(title: str, scoring_config: Dict[str, Any]) -> int:
    lowered = (title or "").lower()
    bonus_per_match = _config_int(scoring_config, "title_trigger_bonus_per_match", 8)
    trigger_words = _config_strings(scoring_config, "title_trigger_words", TITLE_TRIGGER_WORDS)
    return sum(bonus_per_match for keyword in trigger_words if keyword.lower() in lowered)


def _market_priority_bonus(
    title: str,
    group_name: str,
    source_name: str,
    scoring_config: Dict[str, Any],
) -> int:
    haystack = " ".join([title or "", group_name or "", source_name or ""]).lower()
    bonus_per_match = _config_int(scoring_config, "market_priority_bonus_per_match", 14)
    market_priority_keywords = _config_strings(
        scoring_config,
        "market_priority_keywords",
        MARKET_PRIORITY_KEYWORDS,
    )
    bonus = sum(bonus_per_match for keyword in market_priority_keywords if keyword.lower() in haystack)

    source_bonus = scoring_config.get("market_priority_source_bonus", {})
    if isinstance(source_bonus, dict):
        for key, value in source_bonus.items():
            if key in source_name:
                try:
                    bonus += int(value)
                except (TypeError, ValueError):
                    continue
    return bonus


def _focus_constituent_bonus(
    title: str,
    focus_constituents: List[Dict[str, Any]],
    scoring_config: Dict[str, Any],
) -> int:
    focus_config = scoring_config.get("focus_constituents", {})
    if not isinstance(focus_config, dict) or not focus_config.get("enabled", False):
        return 0

    title_norm = _normalize_match_text(title)
    if not title_norm:
        return 0

    default_bonus = _dict_int(focus_config, "bonus_per_match", 48)
    best_bonus = 0
    for item in focus_constituents:
        aliases = item.get("aliases", [])
        if not isinstance(aliases, list):
            continue
        for alias in aliases:
            alias_norm = _normalize_match_text(alias)
            if alias_norm and f" {alias_norm} " in f" {title_norm} ":
                best_bonus = max(best_bonus, _dict_int(item, "focus_bonus", default_bonus))
    return best_bonus


def _non_focus_company_penalty(
    title: str,
    source_name: str,
    group_name: str,
    focus_constituents: List[Dict[str, Any]],
    scoring_config: Dict[str, Any],
) -> int:
    focus_config = scoring_config.get("focus_constituents", {})
    if not isinstance(focus_config, dict) or not focus_config.get("enabled", False):
        return 0

    penalty = _dict_int(focus_config, "non_focus_penalty", 0)
    if penalty <= 0:
        return 0
    if _focus_constituent_bonus(title, focus_constituents, scoring_config) > 0:
        return 0
    if _market_priority_bonus(title, group_name, source_name, scoring_config) > 0:
        return 0

    non_focus_sources = _config_strings(
        focus_config,
        "non_focus_sources",
        ("雅虎财经", "Yahoo"),
    )
    if non_focus_sources and not any(keyword.lower() in (source_name or "").lower() for keyword in non_focus_sources):
        return 0

    exempt_groups = _config_strings(
        focus_config,
        "non_focus_exempt_groups",
        (),
    )
    if any(keyword in (group_name or "") for keyword in exempt_groups):
        return 0

    if _looks_english_title(title):
        return penalty
    return 0


def _low_signal_penalty(title: str, scoring_config: Dict[str, Any]) -> int:
    lowered = (title or "").lower()
    penalty = 0
    penalty_per_match = _config_int(scoring_config, "low_signal_penalty_per_match", 90)
    low_signal_patterns = _config_strings(scoring_config, "low_signal_patterns", LOW_SIGNAL_PATTERNS)
    for pattern in low_signal_patterns:
        if pattern.lower() in lowered:
            penalty += penalty_per_match
    return penalty


def _rank_bonus(ranks: Iterable[Any], scoring_config: Dict[str, Any]) -> int:
    values = [rank for rank in ranks if isinstance(rank, int)]
    if not values:
        return 0
    rank_bonus_base = _config_int(scoring_config, "rank_bonus_base", 24)
    return max(0, rank_bonus_base - min(values))


def _build_candidate(
    title_data: Dict[str, Any],
    group_name: str,
    section_count: int,
    section_percentage: float,
    bucket: str,
    scoring_config: Dict[str, Any],
    focus_constituents: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    title = title_data.get("title", "").strip()
    url = title_data.get("mobile_url") or title_data.get("mobileUrl") or title_data.get("url", "")
    source_name = title_data.get("source_name", "") or title_data.get("feed_name", "")
    count = int(title_data.get("count", 1) or 1)
    is_new = bool(title_data.get("is_new", False))
    percentage_bonus = int(
        max(section_percentage, 0)
        * _config_int(scoring_config, "section_percentage_bonus_multiplier", 20)
    )

    score = 0
    score += _source_weight(source_name, scoring_config)
    score += _group_weight(group_name, scoring_config)
    score += _title_trigger_bonus(title, scoring_config)
    score += _market_priority_bonus(title, group_name, source_name, scoring_config)
    score += _focus_constituent_bonus(title, focus_constituents or [], scoring_config)
    score += _rank_bonus(title_data.get("ranks", []), scoring_config)
    score += min(section_count, _config_int(scoring_config, "section_count_bonus_cap", 10)) * _config_int(
        scoring_config,
        "section_count_bonus_per_item",
        4,
    )
    score += min(count, _config_int(scoring_config, "title_count_bonus_cap", 5)) * _config_int(
        scoring_config,
        "title_count_bonus_per_item",
        6,
    )
    score += percentage_bonus
    if is_new:
        score += _config_int(scoring_config, "is_new_bonus", 30)
    if bucket == "rss":
        score += _config_int(scoring_config, "rss_bonus", 20)
    score -= _non_focus_company_penalty(title, source_name, group_name, focus_constituents or [], scoring_config)
    score -= _low_signal_penalty(title, scoring_config)

    return {
        "title": title,
        "source_name": source_name or ("RSS" if bucket == "rss" else "热榜"),
        "time_display": title_data.get("time_display", ""),
        "first_time": title_data.get("first_time", ""),
        "last_time": title_data.get("last_time", ""),
        "published_at": title_data.get("published_at", ""),
        "url": url,
        "group": group_name,
        "bucket": bucket,
        "score": score,
        "fingerprint": _fingerprint(title, url),
    }


def build_digest_candidates(
    report_data: Dict[str, Any],
    rss_items: Optional[List[Dict[str, Any]]] = None,
    scoring_config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """从热榜统计和 RSS 统计中提取摘要候选项。"""
    resolved_scoring = resolve_scoring_config(scoring_config)
    focus_constituents = _load_focus_constituents(resolved_scoring)
    candidates: List[Dict[str, Any]] = []

    for stat in report_data.get("stats", []):
        section_count = int(stat.get("count", 0) or 0)
        section_percentage = float(stat.get("percentage", 0) or 0)
        for title_data in stat.get("titles", []):
            title = title_data.get("title", "").strip()
            if not title:
                continue
            candidates.append(
                _build_candidate(
                    title_data=title_data,
                    group_name=stat.get("word", "热点"),
                    section_count=section_count,
                    section_percentage=section_percentage,
                    bucket="hotlist",
                    scoring_config=resolved_scoring,
                    focus_constituents=focus_constituents,
                )
            )

    for stat in rss_items or []:
        section_count = int(stat.get("count", 0) or 0)
        section_percentage = float(stat.get("percentage", 0) or 0)
        for title_data in stat.get("titles", []):
            title = title_data.get("title", "").strip()
            if not title:
                continue
            candidates.append(
                _build_candidate(
                    title_data=title_data,
                    group_name=stat.get("word", "RSS"),
                    section_count=section_count,
                    section_percentage=section_percentage,
                    bucket="rss",
                    scoring_config=resolved_scoring,
                    focus_constituents=focus_constituents,
                )
            )

    return candidates


def load_digest_history(
    history_path: Path | str,
    *,
    now: Optional[datetime] = None,
    history_days: Optional[int] = None,
) -> Dict[str, str]:
    """读取已发送历史，并按保留天数清理。"""
    path = Path(history_path)
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    items = data.get("items", [])
    history: Dict[str, str] = {}
    for item in items:
        fingerprint = str(item.get("fingerprint", "")).strip()
        sent_at = str(item.get("sent_at", "")).strip()
        if fingerprint and sent_at:
            history[fingerprint] = sent_at

    if now and history_days:
        cutoff = now - timedelta(days=history_days)
        history = {
            fingerprint: sent_at
            for fingerprint, sent_at in history.items()
            if _align_datetime_for_compare(_parse_iso_datetime(sent_at), cutoff) >= cutoff
        }

    return history


def _parse_iso_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min


def _align_datetime_for_compare(value: datetime, reference: datetime) -> datetime:
    if value.tzinfo is None and reference.tzinfo is not None:
        return value.replace(tzinfo=reference.tzinfo)
    if value.tzinfo is not None and reference.tzinfo is None:
        return value.replace(tzinfo=None)
    return value


def _write_digest_history(history_path: Path, history: Dict[str, str]) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "items": [
            {"fingerprint": fingerprint, "sent_at": sent_at}
            for fingerprint, sent_at in sorted(history.items(), key=lambda item: item[1], reverse=True)
        ]
    }
    history_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def mark_items_as_sent(
    history_path: Path | str,
    items: List[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    history_days: int = 14,
) -> None:
    """将本次摘要条目标记为已发送。"""
    current_time = now or datetime.now()
    path = Path(history_path)
    history = load_digest_history(path, now=current_time, history_days=history_days)
    sent_at = current_time.isoformat()
    for item in items:
        fingerprint = item.get("fingerprint")
        if fingerprint:
            history[str(fingerprint)] = sent_at
    _write_digest_history(path, history)


def select_digest_items(
    candidates: List[Dict[str, Any]],
    *,
    top_k: int = 10,
    sent_history: Optional[Dict[str, str]] = None,
    min_score: int = 0,
    adaptive_config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """选出摘要卡片需要展示的 top N 唯一消息。"""
    sent_history = sent_history or {}
    best_by_fingerprint: Dict[str, Dict[str, Any]] = {}

    for candidate in candidates:
        fingerprint = candidate.get("fingerprint")
        if not fingerprint:
            continue
        existing = best_by_fingerprint.get(fingerprint)
        if not existing or candidate.get("score", 0) > existing.get("score", 0):
            best_by_fingerprint[fingerprint] = candidate

    ordered = sorted(
        best_by_fingerprint.values(),
        key=lambda item: (-int(item.get("score", 0)), item.get("title", "")),
    )

    unsent_items = [item for item in ordered if item["fingerprint"] not in sent_history]
    selected: List[Dict[str, Any]] = []
    for item in unsent_items:
        if int(item.get("score", 0) or 0) < int(min_score or 0):
            continue
        selected.append(item)
        if len(selected) >= top_k:
            break

    adaptive = adaptive_config if isinstance(adaptive_config, dict) else {}
    adaptive_enabled = bool(adaptive.get("enabled", False))
    adaptive_when_empty = bool(adaptive.get("when_empty", True))
    adaptive_when_under_target = bool(adaptive.get("when_under_target", False))
    if not adaptive_enabled or not unsent_items:
        return selected

    try:
        target_items = int(adaptive.get("target_items", 5) or 5)
    except (TypeError, ValueError):
        target_items = 5
    try:
        floor = int(adaptive.get("floor", 0) or 0)
    except (TypeError, ValueError):
        floor = 0

    target_items = max(1, min(target_items, top_k if top_k > 0 else len(unsent_items), len(unsent_items)))
    selected_count = len(selected)
    should_relax = (selected_count == 0 and adaptive_when_empty) or (
        selected_count < target_items and adaptive_when_under_target
    )
    if not should_relax:
        return selected

    relaxed_min_score = max(floor, int(unsent_items[target_items - 1].get("score", 0) or 0))
    if relaxed_min_score < int(min_score or 0):
        reason = "未命中" if selected_count == 0 else f"仅命中 {selected_count} 条"
        print(
            f"[飞书摘要] 固定门槛 {int(min_score or 0)} {reason}，"
            f"降至自适应门槛 {relaxed_min_score}（目标 {target_items} 条）"
        )

    selected = []
    for item in unsent_items:
        if int(item.get("score", 0) or 0) < relaxed_min_score:
            continue
        selected.append(item)
        if len(selected) >= top_k:
            break

    return selected


def _latest_sent_at(sent_history: Optional[Dict[str, str]]) -> Optional[datetime]:
    latest: Optional[datetime] = None
    for sent_at in (sent_history or {}).values():
        parsed = _parse_iso_datetime(str(sent_at or "").strip())
        if parsed == datetime.min:
            continue
        if latest is None or _align_datetime_for_compare(parsed, latest) > latest:
            latest = parsed
    return latest


def _parse_hhmm_on_reference_date(value: str, reference: datetime) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("-", ":")
    match = re.search(r"(\d{1,2}:\d{2})", normalized)
    if not match:
        return None
    try:
        hour_str, minute_str = match.group(1).split(":")
        return reference.replace(
            hour=int(hour_str),
            minute=int(minute_str),
            second=0,
            microsecond=0,
        )
    except ValueError:
        return None


def _candidate_event_time(candidate: Dict[str, Any], reference_now: datetime) -> Optional[datetime]:
    published_at = str(candidate.get("published_at", "") or "").strip()
    if published_at:
        parsed = _parse_iso_datetime(published_at.replace("Z", "+00:00"))
        if parsed != datetime.min:
            return _align_datetime_for_compare(parsed, reference_now)

    for key in ("last_time", "first_time", "time_display"):
        parsed = _parse_hhmm_on_reference_date(str(candidate.get(key, "") or ""), reference_now)
        if parsed is not None:
            return parsed
    return None


def _sort_digest_items_by_beijing_time(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    reference_now = datetime.now(timezone(timedelta(hours=8)))

    def sort_key(item: Dict[str, Any]) -> tuple:
        event_time = _candidate_event_time(item, reference_now)
        if event_time is None:
            event_key = float("inf")
        else:
            event_key = -_align_datetime_for_compare(event_time, reference_now).timestamp()
        return (
            event_key,
            -int(item.get("score", 0) or 0),
            str(item.get("title", "") or ""),
        )

    return sorted(items, key=sort_key)


def select_impact_summary_items(
    candidates: List[Dict[str, Any]],
    *,
    sent_history: Optional[Dict[str, str]] = None,
    now: Optional[datetime] = None,
    max_items: int = 8,
) -> List[Dict[str, Any]]:
    reference_now = now or datetime.now()
    latest_sent_at = _latest_sent_at(sent_history)

    best_by_fingerprint: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        fingerprint = str(candidate.get("fingerprint", "") or "").strip()
        if not fingerprint:
            continue
        existing = best_by_fingerprint.get(fingerprint)
        if not existing or int(candidate.get("score", 0) or 0) > int(existing.get("score", 0) or 0):
            best_by_fingerprint[fingerprint] = candidate

    selected: List[Dict[str, Any]] = []
    for candidate in sorted(
        best_by_fingerprint.values(),
        key=lambda item: (-int(item.get("score", 0) or 0), item.get("title", "")),
    ):
        event_time = _candidate_event_time(candidate, reference_now)
        if latest_sent_at and event_time is not None:
            aligned_event_time = _align_datetime_for_compare(event_time, latest_sent_at)
            if aligned_event_time <= latest_sent_at:
                continue
        selected.append(candidate)
        if len(selected) >= max(1, max_items):
            break
    return selected


def _digest_label(now: datetime) -> str:
    return "晨报" if now.hour < 12 else "晚报"


def _display_timezone(now: Optional[datetime]) -> str:
    if now is None:
        return DEFAULT_TIMEZONE
    tzinfo = getattr(now, "tzinfo", None)
    timezone_name = getattr(tzinfo, "zone", None) or getattr(tzinfo, "key", None)
    return timezone_name or DEFAULT_TIMEZONE


def _normalize_hhmm_text(value: str) -> str:
    matches = re.findall(r"(\d{1,2}[:\-]\d{2})", str(value or ""))
    if not matches:
        return ""
    return matches[-1].replace("-", ":")


def _digest_item_time_display(item: Dict[str, Any], now: Optional[datetime]) -> str:
    timezone = _display_timezone(now)
    published_at = str(item.get("published_at", "") or "").strip()
    if published_at:
        local_display = format_iso_time_friendly(
            published_at,
            timezone=timezone,
            include_date=True,
        )
        return f"北京时间 {local_display}" if local_display else ""

    hhmm = _normalize_hhmm_text(item.get("last_time") or item.get("first_time") or item.get("time_display", ""))
    return f"北京时间 {hhmm}" if hhmm else ""


def _normalize_impact_summary(impact_summary: str) -> str:
    lines: List[str] = []
    for raw_line in str(impact_summary or "").splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        line = re.sub(r"^[-*•]+\s*", "", line)
        line = re.sub(r"^\d+[\.\)、\-\s]+", "", line)
        line = line.strip()
        if line:
            lines.append(line)

    if not lines:
        plain = str(impact_summary or "").strip()
        return plain

    return "\n".join(f"{idx}. {line}" for idx, line in enumerate(lines, 1))


def _build_card_elements(items: List[Dict[str, Any]], now: Optional[datetime]) -> List[Dict[str, Any]]:
    elements: List[Dict[str, Any]] = []
    for idx, item in enumerate(items, 1):
        title = item["title"]
        if item.get("url"):
            title = f"[{title}]({item['url']})"
        meta_parts = [item.get("source_name", ""), item.get("group", ""), _digest_item_time_display(item, now)]
        meta = " · ".join(part for part in meta_parts if part)
        content = f"**{idx}. {title}**"
        if meta:
            content += f"\n<font color='grey'>{meta}</font>"
        elements.append({"tag": "markdown", "content": content})

    return elements


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _looks_english_title(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw or _contains_cjk(raw):
        return False
    english_letters = len(re.findall(r"[A-Za-z]", raw))
    return english_letters >= max(6, len(raw) // 5)


def _import_llm_client():
    try:
        from core_common_tools.llm_client import chat_completion_or_raise

        return chat_completion_or_raise
    except Exception:
        pass

    sibling_repo = Path(__file__).resolve().parents[3] / "core-common-tools"
    if sibling_repo.exists():
        sibling_path = str(sibling_repo)
        if sibling_path not in sys.path:
            sys.path.insert(0, sibling_path)
        try:
            from core_common_tools.llm_client import chat_completion_or_raise

            return chat_completion_or_raise
        except Exception:
            return None
    return None


def _translate_texts_to_chinese(texts: List[str]) -> List[str]:
    llm_call = _import_llm_client()
    if not llm_call:
        return texts

    numbered = "\n".join(f"[{idx}] {text}" for idx, text in enumerate(texts, 1))
    prompt = (
        "把下面的英文财经新闻标题翻译成简洁准确的中文。"
        "保留股票简称、公司名、宏观术语。"
        "按原顺序返回 JSON 数组，只返回数组，不要解释。\n\n"
        f"{numbered}"
    )
    try:
        response = llm_call(prompt, temperature=0.1, timeout=60)
        parsed = json.loads(response)
        if isinstance(parsed, list) and len(parsed) == len(texts):
            results = []
            for original, translated in zip(texts, parsed):
                translated_text = str(translated or "").strip()
                results.append(translated_text or original)
            return results
    except Exception:
        return texts
    return texts


def maybe_translate_digest_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    english_indices: List[int] = []
    english_titles: List[str] = []
    for idx, item in enumerate(items):
        title = str(item.get("title", "")).strip()
        if _looks_english_title(title):
            english_indices.append(idx)
            english_titles.append(title)

    if not english_titles:
        return items

    translated_titles = _translate_texts_to_chinese(english_titles)
    new_items = [dict(item) for item in items]
    for idx, translated in zip(english_indices, translated_titles):
        translated_text = str(translated or "").strip()
        if translated_text:
            new_items[idx]["title"] = translated_text
    return new_items


def generate_digest_impact_summary(
    candidates: List[Dict[str, Any]],
    *,
    sent_history: Optional[Dict[str, str]] = None,
    now: Optional[datetime] = None,
    max_items: int = 8,
) -> str:
    llm_call = _import_llm_client()
    if not llm_call:
        return ""

    reference_now = now or datetime.now()
    latest_sent_at = _latest_sent_at(sent_history)
    window_items = select_impact_summary_items(
        candidates,
        sent_history=sent_history,
        now=reference_now,
        max_items=max_items,
    )
    if not window_items:
        return ""

    lines = []
    for idx, item in enumerate(window_items, 1):
        meta_parts = [item.get("group", ""), item.get("source_name", ""), item.get("time_display", "")]
        meta = " | ".join(part for part in meta_parts if part)
        line = f"{idx}. {item['title']}"
        if meta:
            line += f" | {meta}"
        lines.append(line)

    start_label = latest_sent_at.strftime("%Y-%m-%d %H:%M") if latest_sent_at else "本轮监控开始"
    end_label = reference_now.strftime("%Y-%m-%d %H:%M")
    prompt = (
        f"请基于下面从 {start_label} 到 {end_label} 之间的财经事件，"
        "用中文输出 2 到 3 条最重要的市场影响总结。"
        "重点覆盖 A 股、港股、美股、宏观、利率、汇率、黄金、白银、原油等大宗商品。"
        "只输出简洁项目符号，每条一行，不要前言，不要重复标题。\n\n"
        + "\n".join(lines)
    )
    try:
        return str(llm_call(prompt, temperature=0.2, timeout=60) or "").strip()
    except Exception:
        return ""


def build_digest_payload(
    *,
    webhook_url: str,
    items: List[Dict[str, Any]],
    now: datetime,
    title_prefix: str = "财经资讯",
    impact_summary: str = "",
) -> Dict[str, Any]:
    """根据 webhook 类型构建飞书摘要消息。"""
    label = _digest_label(now)
    title = f"{title_prefix}{label}"
    normalized_summary = _normalize_impact_summary(impact_summary)
    ordered_items = _sort_digest_items_by_beijing_time(items)

    if "www.feishu.cn" in webhook_url:
        lines = [f"【{title}】", ""]
        if normalized_summary:
            lines.extend(["两次推送间重大影响：", normalized_summary, ""])
        for idx, item in enumerate(ordered_items, 1):
            lines.append(f"{idx}. {item['title']}")
            meta_parts = [item.get("source_name", ""), item.get("group", ""), _digest_item_time_display(item, now)]
            meta = " · ".join(part for part in meta_parts if part)
            if meta:
                lines.append(meta)
            if item.get("url"):
                lines.append(item["url"])
            lines.append("")
        return {
            "msg_type": "text",
            "content": {"text": "\n".join(lines).strip()},
        }

    elements = _build_card_elements(ordered_items, now)
    if normalized_summary:
        elements.insert(0, {"tag": "markdown", "content": f"**两次推送间重大影响**\n{normalized_summary}"})

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "subtitle": {"tag": "plain_text", "content": now.strftime("%Y-%m-%d %H:%M")},
                "template": "blue",
            },
            "body": {
                "elements": elements,
            },
        },
    }


def build_openclaw_card_json(
    *,
    items: List[Dict[str, Any]],
    now: datetime,
    title_prefix: str = "财经资讯",
    impact_summary: str = "",
) -> str:
    """构造 OpenClaw 直发飞书所需的 card JSON。"""
    label = _digest_label(now)
    normalized_summary = _normalize_impact_summary(impact_summary)
    ordered_items = _sort_digest_items_by_beijing_time(items)
    card = {
        "header": {
            "title": {"tag": "plain_text", "content": f"{title_prefix}{label}"},
            "template": "blue",
        },
        "elements": [],
    }

    if normalized_summary:
        card["elements"].append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**两次推送间重大影响**\n{normalized_summary}",
                },
            }
        )
        card["elements"].append({"tag": "hr"})

    for idx, item in enumerate(ordered_items, 1):
        title = item["title"]
        if item.get("url"):
            title = f"[{title}]({item['url']})"
        meta_parts = [item.get("source_name", ""), item.get("group", ""), _digest_item_time_display(item, now)]
        meta = " · ".join(part for part in meta_parts if part)
        content = f"**{idx}. {title}**"
        if meta:
            content += f"\n<font color='grey'>{meta}</font>"
        card["elements"].append(
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": content},
            }
        )
    return json.dumps(card, ensure_ascii=False)


def send_feishu_digest(
    *,
    webhook_url: str,
    report_data: Dict[str, Any],
    rss_items: Optional[List[Dict[str, Any]]],
    proxy_url: Optional[str],
    get_time_func,
    digest_config: Dict[str, Any],
    account_label: str = "",
) -> bool:
    """发送飞书财经摘要卡片。"""
    now = get_time_func() if get_time_func else datetime.now()
    top_k = max(1, min(int(digest_config.get("TOP_K", 10) or 10), 10))
    history_days = max(1, int(digest_config.get("HISTORY_DAYS", 14) or 14))
    history_path = build_history_path_for_webhook(
        digest_config.get("HISTORY_FILE") or DEFAULT_HISTORY_FILE,
        webhook_url,
    )
    title_prefix = str(digest_config.get("TITLE", "财经资讯")).strip() or "财经资讯"
    scoring_config = resolve_scoring_config(digest_config.get("SCORING"))
    impact_summary_config = digest_config.get("IMPACT_SUMMARY", {}) or {}

    candidates = build_digest_candidates(report_data, rss_items, scoring_config=scoring_config)
    sent_history = load_digest_history(history_path, now=now, history_days=history_days)
    items = select_digest_items(
        candidates,
        top_k=top_k,
        sent_history=sent_history,
        min_score=_config_int(scoring_config, "minimum_score", 0),
        adaptive_config=scoring_config.get("adaptive_min_score"),
    )

    log_prefix = f"飞书{account_label}" if account_label else "飞书"
    if not items:
        print(f"{log_prefix}摘要卡片跳过：没有新的高优先级消息")
        return False

    impact_summary = ""
    if impact_summary_config.get("ENABLED", False):
        impact_summary = generate_digest_impact_summary(
            candidates,
            sent_history=sent_history,
            now=now,
            max_items=int(impact_summary_config.get("MAX_ITEMS", 8) or 8),
        )

    payload = build_digest_payload(
        webhook_url=webhook_url,
        items=maybe_translate_digest_items(items),
        now=now,
        title_prefix=title_prefix,
        impact_summary=impact_summary,
    )

    headers = {"Content-Type": "application/json"}
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    try:
        response = requests.post(
            webhook_url,
            headers=headers,
            json=payload,
            proxies=proxies,
            timeout=30,
        )
        if response.status_code != 200:
            print(f"{log_prefix}摘要卡片发送失败，状态码：{response.status_code}")
            return False

        result = response.json()
        if result.get("StatusCode") == 0 or result.get("code") == 0:
            mark_items_as_sent(history_path, items, now=now, history_days=history_days)
            print(f"{log_prefix}摘要卡片发送成功，共 {len(items)} 条")
            return True

        error_msg = result.get("msg") or result.get("StatusMessage", "未知错误")
        print(f"{log_prefix}摘要卡片发送失败，错误：{error_msg}")
        return False
    except Exception as exc:
        print(f"{log_prefix}摘要卡片发送出错：{exc}")
        return False


def send_openclaw_digest(
    *,
    report_data: Dict[str, Any],
    rss_items: Optional[List[Dict[str, Any]]],
    openclaw_config: Dict[str, Any],
    digest_config: Dict[str, Any],
    get_time_func,
    account_label: str = "",
) -> bool:
    """通过 OpenClaw 的 Feishu 路由发送摘要卡片。"""
    now = get_time_func() if get_time_func else datetime.now()
    top_k = max(1, min(int(digest_config.get("TOP_K", 10) or 10), 10))
    history_days = max(1, int(digest_config.get("HISTORY_DAYS", 14) or 14))
    title_prefix = str(digest_config.get("TITLE", "财经资讯")).strip() or "财经资讯"
    account = str(openclaw_config.get("ACCOUNT") or openclaw_config.get("AGENT") or "").strip()
    target = str(openclaw_config.get("TARGET") or "").strip() or _resolve_recent_openclaw_target(account)
    openclaw_bin = _resolve_openclaw_bin(str(openclaw_config.get("OPENCLAW_BIN") or "").strip())

    log_prefix = f"飞书{account_label}" if account_label else "飞书"
    if not openclaw_bin:
        print(f"{log_prefix}摘要卡片发送失败：openclaw binary not found")
        return False
    if not account:
        print(f"{log_prefix}摘要卡片发送失败：OpenClaw account is empty")
        return False
    if not target:
        print(f"{log_prefix}摘要卡片发送失败：未找到 OpenClaw Feishu reply target")
        return False

    route_key = f"openclaw:{account}:{target}"
    history_path = build_history_path_for_route(
        digest_config.get("HISTORY_FILE") or DEFAULT_HISTORY_FILE,
        route_key,
    )
    scoring_config = resolve_scoring_config(digest_config.get("SCORING"))
    impact_summary_config = digest_config.get("IMPACT_SUMMARY", {}) or {}
    candidates = build_digest_candidates(report_data, rss_items, scoring_config=scoring_config)
    sent_history = load_digest_history(history_path, now=now, history_days=history_days)
    items = select_digest_items(
        candidates,
        top_k=top_k,
        sent_history=sent_history,
        min_score=_config_int(scoring_config, "minimum_score", 0),
        adaptive_config=scoring_config.get("adaptive_min_score"),
    )
    if not items:
        print(f"{log_prefix}摘要卡片跳过：没有新的高优先级消息")
        return False

    translated_items = maybe_translate_digest_items(items)
    impact_summary = ""
    if impact_summary_config.get("ENABLED", False):
        impact_summary = generate_digest_impact_summary(
            candidates,
            sent_history=sent_history,
            now=now,
            max_items=int(impact_summary_config.get("MAX_ITEMS", 8) or 8),
        )
    card_json = build_openclaw_card_json(
        items=translated_items,
        now=now,
        title_prefix=title_prefix,
        impact_summary=impact_summary,
    )
    command = [
        openclaw_bin,
        "message",
        "send",
        "--channel",
        "feishu",
        "--account",
        account,
        "--target",
        target,
        "--card",
        card_json,
    ]
    proc = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode == 0:
        mark_items_as_sent(history_path, items, now=now, history_days=history_days)
        print(f"{log_prefix}摘要卡片发送成功，共 {len(items)} 条")
        return True

    print(f"{log_prefix}摘要卡片发送失败：{proc.stderr[:200] or proc.stdout[:200]}")
    return False
