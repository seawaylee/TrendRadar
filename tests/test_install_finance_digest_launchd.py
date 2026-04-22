import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.install_finance_digest_launchd import (
    JOB_LABEL,
    build_launchd_payload,
    install_launchd_job,
)


class InstallFinanceDigestLaunchdTests(unittest.TestCase):
    def test_build_launchd_payload_uses_quarter_hour_schedule_and_repo_python(self):
        payload = build_launchd_payload(
            python_bin="/repo/.venv/bin/python",
            repo_root=Path("/repo"),
            log_dir=Path("/repo/logs"),
        )

        self.assertEqual(payload["Label"], JOB_LABEL)
        self.assertEqual(
            payload["ProgramArguments"],
            ["/repo/.venv/bin/python", "-m", "trendradar"],
        )
        self.assertEqual(payload["WorkingDirectory"], "/repo")
        self.assertEqual(
            payload["StartCalendarInterval"],
            [{"Minute": 0}, {"Minute": 15}, {"Minute": 30}, {"Minute": 45}],
        )
        self.assertEqual(payload["EnvironmentVariables"]["TZ"], "Asia/Shanghai")
        self.assertEqual(payload["EnvironmentVariables"]["OPEN_BROWSER"], "false")

    @patch("scripts.install_finance_digest_launchd.subprocess.run")
    def test_install_launchd_job_writes_plist_without_touching_other_projects_jobs(self, mock_run):
        with tempfile.TemporaryDirectory() as tmpdir:
            launch_agents_dir = Path(tmpdir) / "LaunchAgents"
            repo_root = Path(tmpdir) / "TrendRadar"
            log_dir = repo_root / "logs"
            launch_agents_dir.mkdir(parents=True, exist_ok=True)
            log_dir.mkdir(parents=True, exist_ok=True)

            plist_path = install_launchd_job(
                python_bin="/repo/.venv/bin/python",
                repo_root=repo_root,
                launch_agents_dir=launch_agents_dir,
            )

            self.assertTrue(plist_path.exists())
            payload = plistlib.loads(plist_path.read_bytes())
            self.assertEqual(payload["Label"], JOB_LABEL)

            commands = [call.args[0] for call in mock_run.call_args_list]
            self.assertIn(["launchctl", "unload", str(plist_path)], commands)
            self.assertIn(["launchctl", "load", str(plist_path)], commands)
            self.assertIn(["launchctl", "enable", f"gui/{os.getuid()}/{JOB_LABEL}"], commands)
            self.assertFalse(
                any("com.stock_daily_report.finance_brief_0800" in " ".join(command) for command in commands)
            )
            self.assertFalse(
                any("com.stock_daily_report.finance_brief_2000" in " ".join(command) for command in commands)
            )


if __name__ == "__main__":
    unittest.main()
