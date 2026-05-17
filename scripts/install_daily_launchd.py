"""Generate AND load macOS launchd plists for stockbot.

Two jobs are installed:
  1. com.stockbot.morning-call  — 09:20 weekday: read yesterday's watch list and push集合竞价提示
  2. com.stockbot.daily-paper   — 14:25 weekday: run full pipeline (universe→context→pool→trader→report)

Pass --no-load to only write the plists.
"""
from __future__ import annotations

import argparse
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"


def _launchctl(*args: str) -> tuple[int, str, str]:
    proc = subprocess.run(["launchctl", *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _build_plist(label: str, program_args: list[str], hour: int, minute: int) -> dict:
    return {
        "Label": label,
        "ProgramArguments": program_args,
        "WorkingDirectory": str(PROJECT_ROOT),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(PROJECT_ROOT / "stockbot" / "logs" / f"{label}.out"),
        "StandardErrorPath": str(PROJECT_ROOT / "stockbot" / "logs" / f"{label}.err"),
        "RunAtLoad": False,
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin",
            "LANG": "en_US.UTF-8",
        },
    }


def _write(plist: dict) -> Path:
    out = LAUNCH_AGENTS / f"{plist['Label']}.plist"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as f:
        plistlib.dump(plist, f)
    return out


def _try_load(plist_path: Path) -> bool:
    if shutil.which("launchctl") is None:
        return False
    rc, out, err = _launchctl("unload", str(plist_path))
    rc, out, err = _launchctl("load", str(plist_path))
    if rc != 0:
        print(f"[{plist_path.name}] load_FAILED rc={rc} stderr={err.strip()}", file=sys.stderr)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily-hour", type=int, default=14)
    parser.add_argument("--daily-minute", type=int, default=25)
    parser.add_argument("--morning-hour", type=int, default=9)
    parser.add_argument("--morning-minute", type=int, default=20)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--no-load", action="store_true")
    parser.add_argument("--no-morning", action="store_true", help="不安装 morning_call")
    args = parser.parse_args()

    if not PYTHON.exists():
        print(f"FATAL missing venv python: {PYTHON}", file=sys.stderr)
        return 2

    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "stockbot" / "logs").mkdir(parents=True, exist_ok=True)

    plists: list[Path] = []

    # 1. daily-paper (14:25)
    daily = _build_plist(
        "com.stockbot.daily-paper",
        [
            str(PYTHON),
            str(PROJECT_ROOT / "scripts" / "daily_run.py"),
            "--limit", str(args.limit),
            "--top", str(args.top),
        ],
        args.daily_hour, args.daily_minute,
    )
    p1 = _write(daily)
    plists.append(p1)
    print(f"plist_written={p1} time={args.daily_hour:02d}:{args.daily_minute:02d}")

    # 2. morning-call (9:20)
    if not args.no_morning:
        morning = _build_plist(
            "com.stockbot.morning-call",
            [
                str(PYTHON),
                str(PROJECT_ROOT / "scripts" / "morning_call.py"),
                "--config", "config.yaml",
            ],
            args.morning_hour, args.morning_minute,
        )
        p2 = _write(morning)
        plists.append(p2)
        print(f"plist_written={p2} time={args.morning_hour:02d}:{args.morning_minute:02d}")

    if args.no_load:
        for p in plists:
            print(f"manual_load_command=launchctl load {p}")
        return 0

    ok_count = 0
    for p in plists:
        if _try_load(p):
            ok_count += 1
            print(f"launchctl_load=ok label={p.stem}")
        else:
            print(f"launchctl_load=FAILED label={p.stem} -- 见 scripts/install_launchd_help.txt")

    rc, out, err = _launchctl("list")
    for label in ["com.stockbot.daily-paper", "com.stockbot.morning-call"]:
        if label in out:
            print(f"verify=registered {label}")
        elif label == "com.stockbot.morning-call" and args.no_morning:
            continue
        else:
            print(f"verify=NOT_FOUND {label}", file=sys.stderr)
    return 0 if ok_count == len(plists) else 1


if __name__ == "__main__":
    raise SystemExit(main())
