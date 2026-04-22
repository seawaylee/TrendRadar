#!/usr/bin/env python3
"""Install launchd for TrendRadar finance digest collection and scheduled push."""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


JOB_LABEL = "com.trendradar.finance_digest"
LEGACY_LABELS: tuple[str, ...] = ()
PATH_VALUE = (
    f"{Path.home() / '.volta/bin'}:"
    f"{Path.home() / '.local/share/node-v22/bin'}:"
    f"{Path.home() / '.local/bin'}:"
    "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
)
SCHEDULE_MINUTES = (0, 15, 30, 45)


def build_launchd_payload(*, python_bin: str, repo_root: Path, log_dir: Path) -> dict[str, Any]:
    return {
        "Label": JOB_LABEL,
        "ProgramArguments": [python_bin, "-m", "trendradar"],
        "WorkingDirectory": str(repo_root),
        "EnvironmentVariables": {
            "PATH": PATH_VALUE,
            "TZ": "Asia/Shanghai",
            "OPEN_BROWSER": "false",
        },
        "StartCalendarInterval": [{"Minute": minute} for minute in SCHEDULE_MINUTES],
        "StandardOutPath": str(log_dir / "finance_digest_scheduler.out.log"),
        "StandardErrorPath": str(log_dir / "finance_digest_scheduler.err.log"),
        "RunAtLoad": False,
    }


def _launchctl(*args: str) -> None:
    subprocess.run(["launchctl", *args], check=False, capture_output=True, text=True)


def _disable_legacy_jobs(launch_agents_dir: Path) -> None:
    uid = os.getuid()
    for label in LEGACY_LABELS:
        plist_path = launch_agents_dir / f"{label}.plist"
        if plist_path.exists():
            _launchctl("unload", str(plist_path))
        _launchctl("disable", f"gui/{uid}/{label}")


def install_launchd_job(
    *,
    python_bin: str,
    repo_root: Path,
    launch_agents_dir: Path | None = None,
) -> Path:
    resolved_launch_agents_dir = (launch_agents_dir or (Path.home() / "Library/LaunchAgents")).expanduser()
    log_dir = repo_root / "logs"
    resolved_launch_agents_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    payload = build_launchd_payload(
        python_bin=python_bin,
        repo_root=repo_root,
        log_dir=log_dir,
    )
    plist_path = resolved_launch_agents_dir / f"{JOB_LABEL}.plist"
    plist_path.write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_XML))

    _disable_legacy_jobs(resolved_launch_agents_dir)
    _launchctl("enable", f"gui/{os.getuid()}/{JOB_LABEL}")
    _launchctl("unload", str(plist_path))
    _launchctl("load", str(plist_path))
    return plist_path


def _resolve_python_bin(explicit_python: str) -> str:
    explicit = str(explicit_python or "").strip()
    if explicit:
        return explicit
    current = sys.executable
    if current and Path(current).exists():
        return current
    which_python = shutil.which("python3") or shutil.which("python") or ""
    if which_python:
        return which_python
    raise RuntimeError("python executable not found")


def main() -> int:
    parser = argparse.ArgumentParser(description="Install TrendRadar finance digest launchd job.")
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="TrendRadar repo root",
    )
    parser.add_argument(
        "--python-bin",
        default="",
        help="python executable for scheduled runs; defaults to current interpreter",
    )
    parser.add_argument(
        "--launch-agents-dir",
        default="",
        help="override launch agents directory for testing",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    python_bin = _resolve_python_bin(args.python_bin)
    launch_agents_dir = Path(args.launch_agents_dir).expanduser().resolve() if args.launch_agents_dir else None
    plist_path = install_launchd_job(
        python_bin=python_bin,
        repo_root=repo_root,
        launch_agents_dir=launch_agents_dir,
    )

    print(f"Installed launchd job: {JOB_LABEL}")
    print(f"plist: {plist_path}")
    print(f"python: {python_bin}")
    print("schedule: every 15 minutes (:00/:15/:30/:45); push windows still controlled by finance_digest_card")
    if LEGACY_LABELS:
        print("disabled legacy jobs:")
        for label in LEGACY_LABELS:
            print(f"  - {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
