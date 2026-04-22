#!/usr/bin/env python3
"""Apply the finance_max TrendRadar preset without clobbering runtime secrets."""

from __future__ import annotations

import argparse
import json
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


REQUIRED_PRESET_FILES = (
    "config.yaml",
    "timeline.yaml",
    "frequency_words.txt",
    "ai_interests.txt",
    "feishu_digest_scoring.json",
)

PRESERVE_PATHS = (
    ("app", "timezone"),
    ("notification", "channels", "feishu", "webhook_url"),
    ("notification", "channels", "feishu", "card_digest", "scoring_file"),
    ("notification", "channels", "feishu", "openclaw", "enabled"),
    ("notification", "channels", "feishu", "openclaw", "agent"),
    ("notification", "channels", "feishu", "openclaw", "account"),
    ("notification", "channels", "feishu", "openclaw", "target"),
    ("notification", "channels", "feishu", "openclaw", "openclaw_bin"),
    ("notification", "channels", "dingtalk", "webhook_url"),
    ("notification", "channels", "wework", "webhook_url"),
    ("notification", "channels", "wework", "msg_type"),
    ("notification", "channels", "telegram", "bot_token"),
    ("notification", "channels", "telegram", "chat_id"),
    ("notification", "channels", "email", "from"),
    ("notification", "channels", "email", "password"),
    ("notification", "channels", "email", "to"),
    ("notification", "channels", "email", "smtp_server"),
    ("notification", "channels", "email", "smtp_port"),
    ("notification", "channels", "ntfy", "server_url"),
    ("notification", "channels", "ntfy", "topic"),
    ("notification", "channels", "ntfy", "token"),
    ("notification", "channels", "bark", "url"),
    ("notification", "channels", "slack", "webhook_url"),
    ("notification", "channels", "generic_webhook", "webhook_url"),
    ("notification", "channels", "generic_webhook", "payload_template"),
    ("storage", "backend"),
    ("storage", "local"),
    ("storage", "remote"),
    ("storage", "pull"),
    ("ai", "api_key"),
    ("ai", "api_base"),
    ("advanced", "crawler", "use_proxy"),
    ("advanced", "crawler", "default_proxy"),
    ("advanced", "rss", "use_proxy"),
    ("advanced", "rss", "proxy_url"),
)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _get_nested(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for segment in path:
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return deepcopy(current)


def _set_nested(payload: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = payload
    for segment in path[:-1]:
        child = current.get(segment)
        if not isinstance(child, dict):
            child = {}
            current[segment] = child
        current = child
    current[path[-1]] = deepcopy(value)


def _copy_if_exists(source: Path, target: Path) -> None:
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _backup_existing_config(repo_root: Path, preset_name: str) -> Path:
    config_dir = repo_root / "config"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = config_dir / "backups" / f"{preset_name}-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    for name in REQUIRED_PRESET_FILES:
        _copy_if_exists(config_dir / name, backup_dir / name)

    custom_dir = config_dir / "custom"
    if custom_dir.exists():
        shutil.copytree(custom_dir, backup_dir / "custom", dirs_exist_ok=True)

    return backup_dir


def _validate_preset_dir(preset_dir: Path) -> None:
    missing = [name for name in REQUIRED_PRESET_FILES if not (preset_dir / name).exists()]
    if missing:
        raise FileNotFoundError(f"preset is incomplete: missing {', '.join(missing)}")


def apply_finance_max_preset(repo_root: str | Path, preset_name: str = "finance_max") -> dict[str, Any]:
    repo_root = Path(repo_root).expanduser().resolve()
    config_dir = repo_root / "config"
    preset_dir = config_dir / "presets" / preset_name
    _validate_preset_dir(preset_dir)

    backup_dir = _backup_existing_config(repo_root, preset_name)

    target_config_path = config_dir / "config.yaml"
    target_config = _read_yaml(target_config_path)
    preset_config = _read_yaml(preset_dir / "config.yaml")
    merged_config = deepcopy(preset_config)

    for path in PRESERVE_PATHS:
        value = _get_nested(target_config, path)
        if value is not None:
            _set_nested(merged_config, path, value)

    _write_yaml(target_config_path, merged_config)

    for name in ("timeline.yaml", "frequency_words.txt", "ai_interests.txt", "feishu_digest_scoring.json"):
        _copy_if_exists(preset_dir / name, config_dir / name)

    for subdir in ("keyword", "ai"):
        source_dir = preset_dir / "custom" / subdir
        if source_dir.exists():
            shutil.copytree(source_dir, config_dir / "custom" / subdir, dirs_exist_ok=True)

    return {
        "repo_root": str(repo_root),
        "preset_name": preset_name,
        "backup_dir": str(backup_dir),
        "applied_files": [
            str(config_dir / "config.yaml"),
            str(config_dir / "timeline.yaml"),
            str(config_dir / "frequency_words.txt"),
            str(config_dir / "ai_interests.txt"),
            str(config_dir / "feishu_digest_scoring.json"),
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply the TrendRadar finance_max preset.")
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="TrendRadar repo root (defaults to this script's parent repo)",
    )
    parser.add_argument(
        "--preset-name",
        default="finance_max",
        help="preset directory name under config/presets/",
    )
    args = parser.parse_args()

    result = apply_finance_max_preset(args.repo_root, preset_name=args.preset_name)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
