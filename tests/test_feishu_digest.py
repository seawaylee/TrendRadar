import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from unittest.mock import Mock, patch

from trendradar.core.scheduler import Scheduler
from trendradar.notification.feishu_digest import (
    INVESCO_QQQ_ABOUT_URL,
    _fetch_eastmoney_us_china_board_constituents,
    _fetch_eastmoney_us_famous_board_constituents,
    _focus_constituent_bonus,
    _load_focus_constituents,
    build_digest_candidates,
    build_history_path_for_webhook,
    build_digest_payload,
    build_openclaw_card_json,
    _fetch_invesco_qqq_constituents,
    generate_digest_impact_summary,
    maybe_translate_digest_items,
    load_digest_history,
    mark_items_as_sent,
    send_openclaw_digest,
    select_impact_summary_items,
    select_digest_items,
)


class FeishuDigestTests(unittest.TestCase):
    def test_select_digest_items_limits_to_top_ten_unique_titles(self):
        report_data = {
            "stats": [
                {
                    "word": "事件触发",
                    "count": 12,
                    "percentage": 0.8,
                    "titles": [
                        {
                            "title": f"重要事件 {idx}",
                            "source_name": "财联社热门",
                            "time_display": "08:00",
                            "count": 1,
                            "ranks": [idx],
                            "rank_threshold": 8,
                            "url": f"https://example.com/{idx}",
                            "mobile_url": "",
                            "is_new": True,
                        }
                        for idx in range(12)
                    ],
                }
            ],
            "new_titles": [],
            "failed_ids": [],
            "total_new_count": 12,
        }

        rss_items = [
            {
                "word": "宏观政策",
                "count": 2,
                "titles": [
                    {
                        "title": "重要事件 3",
                        "source_name": "美联储货币政策",
                        "time_display": "07:59",
                        "count": 1,
                        "ranks": [],
                        "rank_threshold": 8,
                        "url": "https://example.com/duplicate",
                        "mobile_url": "",
                        "is_new": True,
                    }
                ],
            }
        ]

        candidates = build_digest_candidates(report_data, rss_items)
        selected = select_digest_items(candidates, top_k=10)

        self.assertEqual(len(selected), 10)
        self.assertEqual(len({item["fingerprint"] for item in selected}), 10)

    def test_select_digest_items_skips_titles_already_sent_in_history(self):
        report_data = {
            "stats": [
                {
                    "word": "事件触发",
                    "count": 2,
                    "percentage": 0.8,
                    "titles": [
                        {
                            "title": "已经发过的消息",
                            "source_name": "财联社热门",
                            "time_display": "08:00",
                            "count": 1,
                            "ranks": [1],
                            "rank_threshold": 8,
                            "url": "https://example.com/already-sent",
                            "mobile_url": "",
                            "is_new": True,
                        },
                        {
                            "title": "新的重点消息",
                            "source_name": "华尔街见闻",
                            "time_display": "08:05",
                            "count": 1,
                            "ranks": [2],
                            "rank_threshold": 8,
                            "url": "https://example.com/new",
                            "mobile_url": "",
                            "is_new": True,
                        },
                    ],
                }
            ],
            "new_titles": [],
            "failed_ids": [],
            "total_new_count": 2,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = Path(tmpdir) / "digest_history.json"
            candidates = build_digest_candidates(report_data, rss_items=None)
            mark_items_as_sent(history_path, [candidates[0]], now=datetime(2026, 4, 22, 8, 0, 0))

            history = load_digest_history(history_path)
            selected = select_digest_items(candidates, top_k=10, sent_history=history)

            self.assertEqual([item["title"] for item in selected], ["新的重点消息"])

    def test_select_digest_items_respects_minimum_score(self):
        candidates = [
            {"title": "高分消息", "fingerprint": "a", "score": 320},
            {"title": "临界消息", "fingerprint": "b", "score": 300},
            {"title": "低分消息", "fingerprint": "c", "score": 299},
        ]

        selected = select_digest_items(candidates, top_k=10, min_score=300)

        self.assertEqual([item["title"] for item in selected], ["高分消息", "临界消息"])

    def test_load_digest_history_handles_naive_timestamps_with_aware_now(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = Path(tmpdir) / "digest_history.json"
            history_path.write_text(
                '{"items":[{"fingerprint":"abc","sent_at":"2026-04-22T13:26:17.732532"}]}',
                encoding="utf-8",
            )

            aware_now = datetime.fromisoformat("2026-04-22T14:02:43+08:00")
            history = load_digest_history(history_path, now=aware_now, history_days=14)

            self.assertEqual(history["abc"], "2026-04-22T13:26:17.732532")

    def test_build_digest_payload_uses_interactive_card_for_standard_bot_webhook(self):
        items = [
            {
                "title": "新的重点消息",
                "source_name": "华尔街见闻",
                "time_display": "08:05",
                "url": "https://example.com/new",
                "group": "事件触发",
                "fingerprint": "abc",
                "score": 100,
            }
        ]

        payload = build_digest_payload(
            webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
            items=items,
            now=datetime(2026, 4, 22, 8, 0, 0),
            title_prefix="财经资讯",
        )

        self.assertEqual(payload["msg_type"], "interactive")
        self.assertIn("card", payload)

    def test_history_path_is_scoped_by_webhook(self):
        base = Path("output/meta/feishu_digest_history.json")

        scoped_a = build_history_path_for_webhook(base, "https://open.feishu.cn/open-apis/bot/v2/hook/a")
        scoped_b = build_history_path_for_webhook(base, "https://open.feishu.cn/open-apis/bot/v2/hook/a")
        scoped_c = build_history_path_for_webhook(base, "https://open.feishu.cn/open-apis/bot/v2/hook/c")

        self.assertEqual(scoped_a, scoped_b)
        self.assertNotEqual(scoped_a, scoped_c)

    def test_select_impact_summary_items_uses_last_push_window(self):
        report_data = {
            "stats": [
                {
                    "word": "中港股市",
                    "count": 2,
                    "percentage": 0.8,
                    "titles": [
                        {
                            "title": "午后 A 股券商板块异动拉升",
                            "source_name": "财联社热门",
                            "time_display": "[13:20 ~ 13:35]",
                            "first_time": "13:20",
                            "last_time": "13:35",
                            "count": 2,
                            "ranks": [1],
                            "rank_threshold": 8,
                            "url": "https://example.com/ashare",
                            "mobile_url": "",
                            "is_new": True,
                        },
                        {
                            "title": "早盘已经推送过的宏观消息",
                            "source_name": "华尔街见闻",
                            "time_display": "[09:00 ~ 09:10]",
                            "first_time": "09:00",
                            "last_time": "09:10",
                            "count": 1,
                            "ranks": [2],
                            "rank_threshold": 8,
                            "url": "https://example.com/morning",
                            "mobile_url": "",
                            "is_new": True,
                        },
                    ],
                }
            ],
            "new_titles": [],
            "failed_ids": [],
            "total_new_count": 2,
        }
        rss_items = [
            {
                "word": "大宗商品",
                "count": 1,
                "titles": [
                    {
                        "title": "Oil prices rise as tariff talks resume",
                        "source_name": "雅虎财经",
                        "time_display": "04-22 13:10",
                        "published_at": "2026-04-22T13:10:00+08:00",
                        "count": 1,
                        "ranks": [1],
                        "rank_threshold": 8,
                        "url": "https://example.com/oil",
                        "mobile_url": "",
                        "is_new": True,
                    }
                ],
            }
        ]

        candidates = build_digest_candidates(report_data, rss_items)
        sent_history = {"old": "2026-04-22T12:00:00+08:00"}
        window_items = select_impact_summary_items(
            candidates,
            sent_history=sent_history,
            now=datetime.fromisoformat("2026-04-22T14:00:00+08:00"),
            max_items=10,
        )

        self.assertEqual(
            [item["title"] for item in window_items],
            ["午后 A 股券商板块异动拉升", "Oil prices rise as tariff talks resume"],
        )

    @patch("trendradar.notification.feishu_digest._import_llm_client")
    def test_generate_digest_impact_summary_only_uses_window_items(self, mock_import_llm_client):
        llm_call = Mock(return_value="- 午后 A 股风险偏好回升\n- 原油上行推升通胀交易")
        mock_import_llm_client.return_value = llm_call

        report_data = {
            "stats": [
                {
                    "word": "宏观政策",
                    "count": 2,
                    "percentage": 0.8,
                    "titles": [
                        {
                            "title": "午后 A 股券商板块异动拉升",
                            "source_name": "财联社热门",
                            "time_display": "[13:20 ~ 13:35]",
                            "first_time": "13:20",
                            "last_time": "13:35",
                            "count": 2,
                            "ranks": [1],
                            "rank_threshold": 8,
                            "url": "https://example.com/ashare",
                            "mobile_url": "",
                            "is_new": True,
                        },
                        {
                            "title": "早盘已经推送过的宏观消息",
                            "source_name": "华尔街见闻",
                            "time_display": "[09:00 ~ 09:10]",
                            "first_time": "09:00",
                            "last_time": "09:10",
                            "count": 1,
                            "ranks": [2],
                            "rank_threshold": 8,
                            "url": "https://example.com/morning",
                            "mobile_url": "",
                            "is_new": True,
                        },
                    ],
                }
            ],
            "new_titles": [],
            "failed_ids": [],
            "total_new_count": 2,
        }

        candidates = build_digest_candidates(report_data)
        summary = generate_digest_impact_summary(
            candidates,
            sent_history={"old": "2026-04-22T12:00:00+08:00"},
            now=datetime.fromisoformat("2026-04-22T14:00:00+08:00"),
            max_items=6,
        )

        self.assertIn("A 股风险偏好回升", summary)
        prompt = llm_call.call_args.args[0]
        self.assertIn("午后 A 股券商板块异动拉升", prompt)
        self.assertNotIn("早盘已经推送过的宏观消息", prompt)

    def test_build_openclaw_card_json_can_include_impact_summary(self):
        card_json = build_openclaw_card_json(
            items=[
                {
                    "title": "午后 A 股券商板块异动拉升",
                    "source_name": "财联社热门",
                    "time_display": "13:35",
                    "url": "https://example.com/ashare",
                    "group": "中港股市",
                }
            ],
            now=datetime(2026, 4, 22, 22, 0, 0),
            title_prefix="财经资讯",
            impact_summary="- A 股风险偏好回升\n- 原油上行抬升通胀预期",
        )

        self.assertIn("两次推送间重大影响", card_json)
        self.assertIn("A 股风险偏好回升", card_json)

    def test_build_openclaw_card_json_omits_redundant_digest_copy(self):
        card_json = build_openclaw_card_json(
            items=[
                {
                    "title": "午后 A 股券商板块异动拉升",
                    "source_name": "财联社热门",
                    "time_display": "13:35",
                    "url": "https://example.com/ashare",
                    "group": "中港股市",
                }
            ],
            now=datetime(2026, 4, 22, 22, 0, 0),
            title_prefix="财经资讯",
        )

        self.assertNotIn("去重后", card_json)
        self.assertNotIn("自动过滤", card_json)

    def test_build_openclaw_card_json_uses_single_beijing_time_instead_of_range(self):
        card_json = build_openclaw_card_json(
            items=[
                {
                    "title": "午后 A 股券商板块异动拉升",
                    "source_name": "财联社热门",
                    "time_display": "[13:20 ~ 13:35]",
                    "first_time": "13:20",
                    "last_time": "13:35",
                    "url": "https://example.com/ashare",
                    "group": "中港股市",
                },
                {
                    "title": "Oil prices rise as tariff talks resume",
                    "source_name": "雅虎财经",
                    "time_display": "04-22 01:10",
                    "published_at": "2026-04-21T17:10:00+00:00",
                    "url": "https://example.com/oil",
                    "group": "大宗商品",
                },
            ],
            now=datetime.fromisoformat("2026-04-22T22:00:00+08:00"),
            title_prefix="财经资讯",
        )

        self.assertIn("北京时间 13:35", card_json)
        self.assertIn("北京时间 04-22 01:10", card_json)
        self.assertNotIn("[13:20 ~ 13:35]", card_json)

    def test_build_openclaw_card_json_normalizes_impact_summary_to_numbered_lines(self):
        card_json = build_openclaw_card_json(
            items=[
                {
                    "title": "午后 A 股券商板块异动拉升",
                    "source_name": "财联社热门",
                    "time_display": "13:35",
                    "url": "https://example.com/ashare",
                    "group": "中港股市",
                }
            ],
            now=datetime(2026, 4, 22, 22, 0, 0),
            title_prefix="财经资讯",
            impact_summary="- A 股风险偏好回升\n- 原油上行抬升通胀预期",
        )

        self.assertIn("1. A 股风险偏好回升", card_json)
        self.assertIn("2. 原油上行抬升通胀预期", card_json)
        self.assertNotIn("\n- A 股风险偏好回升", card_json)

    def test_macro_and_official_items_rank_above_generic_english_earnings_preview(self):
        report_data = {
            "stats": [
                {
                    "word": "宏观政策",
                    "count": 2,
                    "percentage": 0.9,
                    "titles": [
                        {
                            "title": "美联储主席提名人沃什：货币政策将独立于政治",
                            "source_name": "财联社热门",
                            "time_display": "08:05",
                            "count": 1,
                            "ranks": [1],
                            "rank_threshold": 8,
                            "url": "https://example.com/macro",
                            "mobile_url": "",
                            "is_new": True,
                        }
                    ],
                },
                {
                    "word": "财报与指引",
                    "count": 6,
                    "percentage": 0.9,
                    "titles": [
                        {
                            "title": "Alphabet Q1 Earnings Preview: Is GOOGL Stock a Buy, Sell, or Hold?",
                            "source_name": "雅虎财经",
                            "time_display": "08:06",
                            "count": 1,
                            "ranks": [],
                            "rank_threshold": 8,
                            "url": "https://example.com/preview",
                            "mobile_url": "",
                            "is_new": True,
                        }
                    ],
                },
            ],
            "new_titles": [],
            "failed_ids": [],
            "total_new_count": 2,
        }

        selected = select_digest_items(build_digest_candidates(report_data), top_k=10)
        self.assertEqual(selected[0]["title"], "美联储主席提名人沃什：货币政策将独立于政治")

    def test_custom_scoring_can_change_which_item_ranks_first(self):
        report_data = {
            "stats": [
                {
                    "word": "宏观政策",
                    "count": 2,
                    "percentage": 0.9,
                    "titles": [
                        {
                            "title": "美联储主席提名人沃什：货币政策将独立于政治",
                            "source_name": "财联社热门",
                            "time_display": "08:05",
                            "count": 1,
                            "ranks": [1],
                            "rank_threshold": 8,
                            "url": "https://example.com/macro",
                            "mobile_url": "",
                            "is_new": True,
                        }
                    ],
                },
                {
                    "word": "财报与指引",
                    "count": 6,
                    "percentage": 0.9,
                    "titles": [
                        {
                            "title": "Alphabet Q1 Earnings Preview: Is GOOGL Stock a Buy, Sell, or Hold?",
                            "source_name": "雅虎财经",
                            "time_display": "08:06",
                            "count": 1,
                            "ranks": [],
                            "rank_threshold": 8,
                            "url": "https://example.com/preview",
                            "mobile_url": "",
                            "is_new": True,
                        }
                    ],
                },
            ],
            "new_titles": [],
            "failed_ids": [],
            "total_new_count": 2,
        }

        scoring_config = {
            "group_weights": {"宏观政策": 1, "财报与指引": 200},
            "low_signal_patterns": [],
        }
        selected = select_digest_items(
            build_digest_candidates(report_data, scoring_config=scoring_config),
            top_k=10,
        )
        self.assertEqual(selected[0]["title"], "Alphabet Q1 Earnings Preview: Is GOOGL Stock a Buy, Sell, or Hold?")

    @patch("trendradar.notification.feishu_digest._load_focus_constituents")
    def test_dynamic_focus_constituent_bonus_prioritizes_ndx_spx_heavyweights(self, mock_load_focus_constituents):
        mock_load_focus_constituents.return_value = [
            {"name": "Apple Inc", "aliases": ["apple", "aapl"], "weight": 7.29, "index": "QQQ"},
            {"name": "Microsoft Corp", "aliases": ["microsoft", "msft"], "weight": 5.64, "index": "QQQ"},
        ]

        report_data = {
            "stats": [
                {
                    "word": "核心公司",
                    "count": 2,
                    "percentage": 0.9,
                    "titles": [
                        {
                            "title": "Apple unveils new AI strategy for iPhone ecosystem",
                            "source_name": "雅虎财经",
                            "time_display": "08:05",
                            "count": 1,
                            "ranks": [],
                            "rank_threshold": 8,
                            "url": "https://example.com/apple",
                            "mobile_url": "",
                            "is_new": True,
                        },
                        {
                            "title": "Comcast Trades at Just 8 Times Earnings After Losing 711,000 Broadband Subs Last Year",
                            "source_name": "雅虎财经",
                            "time_display": "08:06",
                            "count": 1,
                            "ranks": [],
                            "rank_threshold": 8,
                            "url": "https://example.com/comcast",
                            "mobile_url": "",
                            "is_new": True,
                        },
                    ],
                }
            ],
            "new_titles": [],
            "failed_ids": [],
            "total_new_count": 2,
        }

        selected = select_digest_items(build_digest_candidates(report_data), top_k=10)
        self.assertEqual(selected[0]["title"], "Apple unveils new AI strategy for iPhone ecosystem")

    @patch("trendradar.notification.feishu_digest._run_json_request")
    @patch("trendradar.notification.feishu_digest.requests.get")
    def test_fetch_invesco_qqq_constituents_uses_browser_like_headers_for_official_api(
        self,
        mock_requests_get,
        mock_run_json_request,
    ):
        mock_requests_get.return_value = Mock(
            status_code=200,
            text='<ul data-holdings-api="https://api.example.com/qqq"></ul>',
        )
        mock_run_json_request.return_value = {
            "holdings": [
                {
                    "issuerName": "Apple Inc",
                    "ticker": "AAPL",
                    "percentageOfTotalNetAssets": 7.29,
                }
            ]
        }

        items = _fetch_invesco_qqq_constituents(1)

        self.assertEqual(items[0]["name"], "Apple Inc")
        call_headers = mock_run_json_request.call_args.kwargs["headers"]
        self.assertEqual(call_headers["Referer"], INVESCO_QQQ_ABOUT_URL)
        self.assertEqual(call_headers["Origin"], "https://www.invesco.com")
        self.assertEqual(call_headers["Accept-Language"], "en-US,en;q=0.9")
        self.assertEqual(call_headers["Sec-Fetch-Mode"], "cors")
        self.assertEqual(call_headers["Sec-Fetch-Site"], "same-site")
        self.assertIn("Chromium", call_headers["sec-ch-ua"])

    @patch("trendradar.notification.feishu_digest.requests.get")
    def test_fetch_eastmoney_us_china_board_constituents_filters_by_market_cap(self, mock_requests_get):
        first_page = Mock()
        first_page.status_code = 200
        first_page.json.return_value = {
            "data": {
                "diff": [
                    {
                        "f13": "106",
                        "f12": "BABA",
                        "f14": "阿里巴巴",
                        "f20": 200_000_000_000,
                        "f3": 1.2,
                    },
                    {
                        "f13": "105",
                        "f12": "BILI",
                        "f14": "哔哩哔哩",
                        "f20": 3_000_000_000,
                        "f3": -0.8,
                    },
                ],
                "total": 2,
            }
        }
        second_page = Mock()
        second_page.status_code = 200
        second_page.json.return_value = {"data": {"diff": []}}
        mock_requests_get.side_effect = [first_page, second_page]

        items = _fetch_eastmoney_us_china_board_constituents(top_k=10, min_market_cap_usd=5_000_000_000)

        self.assertEqual([item["name"] for item in items], ["阿里巴巴"])
        self.assertIn("baba", items[0]["aliases"])

    @patch("trendradar.notification.feishu_digest.requests.get")
    def test_fetch_eastmoney_us_famous_board_constituents_respects_top_k(self, mock_requests_get):
        first_page = Mock()
        first_page.status_code = 200
        first_page.json.return_value = {
            "data": {
                "diff": [
                    {"f13": "105", "f12": "AAPL", "f14": "苹果", "f20": 3_000_000_000_000, "f3": 1.2},
                    {"f13": "105", "f12": "MSFT", "f14": "微软", "f20": 2_800_000_000_000, "f3": 0.8},
                    {"f13": "105", "f12": "TSLA", "f14": "特斯拉", "f20": 800_000_000_000, "f3": -0.5},
                ],
                "total": 3,
            }
        }
        second_page = Mock()
        second_page.status_code = 200
        second_page.json.return_value = {"data": {"diff": []}}
        mock_requests_get.side_effect = [first_page, second_page]

        items = _fetch_eastmoney_us_famous_board_constituents(top_k=2)

        self.assertEqual([item["name"] for item in items], ["苹果", "微软"])
        self.assertIn("aapl", items[0]["aliases"])

    @patch("trendradar.notification.feishu_digest._fetch_eastmoney_us_famous_board_constituents")
    def test_load_focus_constituents_applies_source_specific_top_k_and_bonus(self, mock_fetch_famous):
        mock_fetch_famous.return_value = [
            {"name": "苹果", "weight": 3_000_000_000_000, "aliases": ["apple", "aapl"], "index": "US_FAMOUS_BOARD"}
        ]
        scoring_config = {
            "focus_constituents": {
                "enabled": True,
                "top_k": 10,
                "bonus_per_match": 48,
                "sources": [
                    {"type": "eastmoney_us_famous_board", "top_k": 50, "bonus_per_match": 18}
                ],
            }
        }

        items = _load_focus_constituents(scoring_config)

        mock_fetch_famous.assert_called_once_with(50, min_market_cap_usd=0.0)
        self.assertEqual(items[0]["focus_bonus"], 18)

    def test_focus_constituent_bonus_prefers_higher_bonus_when_alias_duplicates(self):
        scoring_config = {
            "focus_constituents": {
                "enabled": True,
                "bonus_per_match": 48,
            }
        }
        bonus = _focus_constituent_bonus(
            "Apple unveils new AI strategy for iPhone ecosystem",
            [
                {"aliases": ["apple"], "focus_bonus": 18},
                {"aliases": ["apple"], "focus_bonus": 48},
            ],
            scoring_config,
        )

        self.assertEqual(bonus, 48)

    @patch("trendradar.notification.feishu_digest._translate_texts_to_chinese")
    def test_maybe_translate_digest_items_translates_english_titles(self, mock_translate):
        mock_translate.return_value = ["苹果在财报前出现异常看跌期权活动"]
        items = [
            {
                "title": "Apple Has Unusual Put Options Activity Ahead of Earnings",
                "source_name": "雅虎财经",
                "time_display": "08:05",
                "url": "https://example.com/apple",
                "group": "财报与指引",
                "fingerprint": "abc",
                "score": 100,
            }
        ]

        translated = maybe_translate_digest_items(items)
        self.assertEqual(translated[0]["title"], "苹果在财报前出现异常看跌期权活动")

    @patch("trendradar.notification.feishu_digest.subprocess.run")
    @patch("trendradar.notification.feishu_digest._resolve_recent_openclaw_target")
    @patch("trendradar.notification.feishu_digest._resolve_openclaw_bin")
    def test_send_openclaw_digest_uses_recent_crawler_route_and_card(
        self,
        mock_resolve_bin,
        mock_resolve_target,
        mock_run,
    ):
        mock_resolve_bin.return_value = "/usr/local/bin/openclaw"
        mock_resolve_target.return_value = "chat:abc"
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = '{"ok":true}'
        mock_run.return_value.stderr = ""

        report_data = {
            "stats": [
                {
                    "word": "事件触发",
                    "count": 1,
                    "percentage": 0.8,
                    "titles": [
                        {
                            "title": "新的重点消息",
                            "source_name": "华尔街见闻",
                            "time_display": "08:05",
                            "count": 1,
                            "ranks": [1],
                            "rank_threshold": 8,
                            "url": "https://example.com/new",
                            "mobile_url": "",
                            "is_new": True,
                        }
                    ],
                }
            ],
            "new_titles": [],
            "failed_ids": [],
            "total_new_count": 1,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            ok = send_openclaw_digest(
                report_data=report_data,
                rss_items=None,
                openclaw_config={
                    "ENABLED": True,
                    "AGENT": "crawler",
                    "ACCOUNT": "crawler",
                    "TARGET": "",
                    "OPENCLAW_BIN": "/usr/local/bin/openclaw",
                },
                digest_config={
                    "ENABLED": True,
                    "TOP_K": 10,
                    "HISTORY_DAYS": 14,
                    "HISTORY_FILE": str(Path(tmpdir) / "history.json"),
                    "TITLE": "财经资讯",
                },
                get_time_func=lambda: datetime(2026, 4, 22, 8, 0, 0),
                account_label="",
            )

        self.assertTrue(ok)
        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[:3], ["/usr/local/bin/openclaw", "message", "send"])
        self.assertIn("--account", cmd)
        self.assertIn("crawler", cmd)
        self.assertIn("--target", cmd)
        self.assertIn("chat:abc", cmd)
        self.assertIn("--card", cmd)


class FinanceDigestScheduleTests(unittest.TestCase):
    def _make_scheduler(self, now: datetime) -> Scheduler:
        timeline = {
            "presets": {
                "finance_digest_card": {
                    "default": {
                        "collect": True,
                        "analyze": False,
                        "push": False,
                        "report_mode": "current",
                        "ai_mode": "current",
                    },
                    "periods": {
                        "morning_digest": {
                            "name": "财经晨报",
                            "start": "08:00",
                            "end": "08:30",
                            "analyze": True,
                            "push": True,
                            "report_mode": "current",
                            "once": {"analyze": True, "push": True},
                        },
                        "night_digest": {
                            "name": "财经晚报",
                            "start": "22:00",
                            "end": "22:30",
                            "analyze": True,
                            "push": True,
                            "report_mode": "current",
                            "once": {"analyze": True, "push": True},
                        },
                    },
                    "day_plans": {
                        "workday": {"periods": ["morning_digest", "night_digest"]},
                        "weekend": {"periods": ["morning_digest", "night_digest"]},
                    },
                    "week_map": {1: "workday", 2: "workday", 3: "workday", 4: "workday", 5: "workday", 6: "weekend", 7: "weekend"},
                }
            }
        }
        storage = object()
        return Scheduler(
            schedule_config={"enabled": True, "preset": "finance_digest_card"},
            timeline_data=timeline,
            storage_backend=storage,
            get_time_func=lambda: now,
            fallback_report_mode="current",
        )

    def test_scheduler_resolves_morning_digest(self):
        schedule = self._make_scheduler(datetime(2026, 4, 22, 8, 5, 0)).resolve()

        self.assertEqual(schedule.period_key, "morning_digest")
        self.assertTrue(schedule.push)
        self.assertTrue(schedule.once_push)

    def test_scheduler_resolves_night_digest(self):
        schedule = self._make_scheduler(datetime(2026, 4, 22, 22, 5, 0)).resolve()

        self.assertEqual(schedule.period_key, "night_digest")
        self.assertTrue(schedule.push)
        self.assertTrue(schedule.once_push)


if __name__ == "__main__":
    unittest.main()
