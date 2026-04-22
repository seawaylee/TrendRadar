import importlib.util
import tempfile
import unittest
from pathlib import Path

import yaml


def _load_module(module_path: Path):
    spec = importlib.util.spec_from_file_location("apply_finance_max_preset", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ApplyFinanceMaxPresetTests(unittest.TestCase):
    def test_apply_preset_preserves_runtime_settings_and_copies_assets(self):
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "apply_finance_max_preset.py"
        repo_root = Path(tempfile.mkdtemp(prefix="trendradar_finance_preset_test_"))
        config_dir = repo_root / "config"
        preset_dir = config_dir / "presets" / "finance_max"
        (preset_dir / "custom" / "keyword").mkdir(parents=True, exist_ok=True)
        (preset_dir / "custom" / "ai").mkdir(parents=True, exist_ok=True)
        config_dir.mkdir(parents=True, exist_ok=True)

        target_config = {
            "app": {"timezone": "America/New_York"},
            "schedule": {"enabled": True, "preset": "morning_evening"},
            "notification": {
                "enabled": True,
                "channels": {"feishu": {"webhook_url": "https://example.com/hook"}},
            },
            "storage": {
                "backend": "remote",
                "remote": {"bucket_name": "finance-prod"},
            },
            "ai": {
                "model": "deepseek/deepseek-chat",
                "api_key": "sk-live",
                "api_base": "https://ai.example.com/v1",
            },
            "advanced": {
                "crawler": {"use_proxy": True, "default_proxy": "http://127.0.0.1:7890"},
                "rss": {"use_proxy": True, "proxy_url": "http://127.0.0.1:7890"},
            },
        }
        preset_config = {
            "app": {"timezone": "Asia/Shanghai"},
            "schedule": {"enabled": True, "preset": "finance_max"},
            "notification": {
                "enabled": True,
                "channels": {"feishu": {"webhook_url": ""}, "telegram": {"bot_token": ""}},
            },
            "storage": {
                "backend": "local",
                "remote": {"bucket_name": ""},
            },
            "ai": {
                "model": "openai/gpt-4.1",
                "api_key": "",
                "api_base": "",
            },
            "advanced": {
                "crawler": {"use_proxy": False, "default_proxy": ""},
                "rss": {"use_proxy": False, "proxy_url": ""},
            },
        }

        (config_dir / "config.yaml").write_text(yaml.safe_dump(target_config, allow_unicode=True), encoding="utf-8")
        (config_dir / "timeline.yaml").write_text("presets: {}\n", encoding="utf-8")
        (config_dir / "frequency_words.txt").write_text("[WORD_GROUPS]\nold\n", encoding="utf-8")
        (config_dir / "ai_interests.txt").write_text("old interests\n", encoding="utf-8")
        (config_dir / "feishu_digest_scoring.json").write_text('{"group_weights":{"旧分组":1}}\n', encoding="utf-8")

        (preset_dir / "config.yaml").write_text(yaml.safe_dump(preset_config, allow_unicode=True), encoding="utf-8")
        (preset_dir / "timeline.yaml").write_text("presets:\n  finance_max: {}\n", encoding="utf-8")
        (preset_dir / "frequency_words.txt").write_text("[WORD_GROUPS]\nnew\n", encoding="utf-8")
        (preset_dir / "ai_interests.txt").write_text("new interests\n", encoding="utf-8")
        (preset_dir / "feishu_digest_scoring.json").write_text(
            '{"group_weights":{"宏观政策":56},"low_signal_patterns":["earnings preview"]}\n',
            encoding="utf-8",
        )
        (preset_dir / "custom" / "keyword" / "finance_intraday.txt").write_text("[WORD_GROUPS]\nintraday\n", encoding="utf-8")
        (preset_dir / "custom" / "ai" / "finance_deep_dive.txt").write_text("deep dive\n", encoding="utf-8")

        module = _load_module(script_path)
        result = module.apply_finance_max_preset(repo_root)

        merged = yaml.safe_load((config_dir / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(merged["schedule"]["preset"], "finance_max")
        self.assertEqual(merged["app"]["timezone"], "America/New_York")
        self.assertEqual(merged["notification"]["channels"]["feishu"]["webhook_url"], "https://example.com/hook")
        self.assertEqual(merged["storage"]["backend"], "remote")
        self.assertEqual(merged["storage"]["remote"]["bucket_name"], "finance-prod")
        self.assertEqual(merged["ai"]["model"], "openai/gpt-4.1")
        self.assertEqual(merged["ai"]["api_key"], "sk-live")
        self.assertEqual(merged["ai"]["api_base"], "https://ai.example.com/v1")
        self.assertTrue(merged["advanced"]["crawler"]["use_proxy"])
        self.assertEqual(merged["advanced"]["rss"]["proxy_url"], "http://127.0.0.1:7890")

        self.assertEqual((config_dir / "frequency_words.txt").read_text(encoding="utf-8"), "[WORD_GROUPS]\nnew\n")
        self.assertEqual((config_dir / "ai_interests.txt").read_text(encoding="utf-8"), "new interests\n")
        self.assertIn("宏观政策", (config_dir / "feishu_digest_scoring.json").read_text(encoding="utf-8"))
        self.assertTrue((config_dir / "custom" / "keyword" / "finance_intraday.txt").exists())
        self.assertTrue((config_dir / "custom" / "ai" / "finance_deep_dive.txt").exists())
        self.assertTrue(Path(result["backup_dir"]).exists())

    def test_apply_preset_keeps_existing_webhook_and_merges_new_feishu_digest_config(self):
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "apply_finance_max_preset.py"
        repo_root = Path(tempfile.mkdtemp(prefix="trendradar_finance_digest_merge_test_"))
        config_dir = repo_root / "config"
        preset_dir = config_dir / "presets" / "finance_max"
        preset_dir.mkdir(parents=True, exist_ok=True)
        config_dir.mkdir(parents=True, exist_ok=True)

        target_config = {
            "notification": {
                "enabled": True,
                "channels": {
                    "feishu": {"webhook_url": "https://example.com/hook"},
                },
            },
        }
        preset_config = {
            "notification": {
                "enabled": True,
                "channels": {
                    "feishu": {
                        "webhook_url": "",
                        "card_digest": {
                            "enabled": True,
                            "top_k": 10,
                            "history_days": 14,
                            "scoring_file": "config/feishu_digest_scoring.json",
                        },
                    },
                },
            },
        }

        (config_dir / "config.yaml").write_text(yaml.safe_dump(target_config, allow_unicode=True), encoding="utf-8")
        (config_dir / "timeline.yaml").write_text("presets: {}\n", encoding="utf-8")
        (config_dir / "frequency_words.txt").write_text("[WORD_GROUPS]\nold\n", encoding="utf-8")
        (config_dir / "ai_interests.txt").write_text("old interests\n", encoding="utf-8")
        (config_dir / "feishu_digest_scoring.json").write_text('{"group_weights":{"旧分组":1}}\n', encoding="utf-8")

        (preset_dir / "config.yaml").write_text(yaml.safe_dump(preset_config, allow_unicode=True), encoding="utf-8")
        (preset_dir / "timeline.yaml").write_text("presets:\n  finance_max: {}\n", encoding="utf-8")
        (preset_dir / "frequency_words.txt").write_text("[WORD_GROUPS]\nnew\n", encoding="utf-8")
        (preset_dir / "ai_interests.txt").write_text("new interests\n", encoding="utf-8")
        (preset_dir / "feishu_digest_scoring.json").write_text(
            '{"group_weights":{"宏观政策":56},"low_signal_patterns":["earnings preview"]}\n',
            encoding="utf-8",
        )

        module = _load_module(script_path)
        module.apply_finance_max_preset(repo_root)

        merged = yaml.safe_load((config_dir / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(merged["notification"]["channels"]["feishu"]["webhook_url"], "https://example.com/hook")
        self.assertTrue(merged["notification"]["channels"]["feishu"]["card_digest"]["enabled"])
        self.assertEqual(merged["notification"]["channels"]["feishu"]["card_digest"]["top_k"], 10)
        self.assertEqual(
            merged["notification"]["channels"]["feishu"]["card_digest"]["scoring_file"],
            "config/feishu_digest_scoring.json",
        )
        self.assertIn("宏观政策", (config_dir / "feishu_digest_scoring.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
