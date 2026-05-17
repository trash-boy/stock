"""Daily paper-trading workflow.

Pipeline:
1. Build A-share universe from AkShare spot data.
2. Run strategy on top candidates.
3. Execute through PaperBroker / LarkAlertBroker.
4. Persist daily logs and summary JSON.
5. On any step failure: push Lark webhook alert (best effort).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _try_import_calendar():
    try:
        from stockbot.utils.calendar import is_trade_day  # type: ignore
        return is_trade_day
    except Exception:
        return None


def _try_load_cfg(config_path: str):
    try:
        from stockbot.core.config import load_config  # type: ignore
        return load_config(config_path)
    except Exception:
        return {}


def _push_lark_failure(cfg: dict, run_id: str, step: str, log_path: Path, tail: str) -> None:
    """Best-effort Lark webhook failure alert. Never raises."""
    try:
        webhook = (cfg.get("lark", {}) or {}).get("webhook", "").strip()
        if not webhook:
            return
        import urllib.request
        msg = (
            f"❌ stockbot daily_run FAILED\n"
            f"run_id={run_id}\n"
            f"step={step}\n"
            f"log={log_path}\n"
            f"---tail---\n{tail[-500:]}"
        )
        body = json.dumps({"msg_type": "text", "content": {"text": msg}}).encode("utf-8")
        req = urllib.request.Request(
            webhook, data=body, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=8).read()
    except Exception:
        pass


def _push_lark_info(cfg: dict, text: str) -> None:
    try:
        webhook = (cfg.get("lark", {}) or {}).get("webhook", "").strip()
        if not webhook:
            return
        import urllib.request
        body = json.dumps({"msg_type": "text", "content": {"text": text}}).encode("utf-8")
        req = urllib.request.Request(
            webhook, data=body, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=8).read()
    except Exception:
        pass


def run_step(name: str, command: list[str], timeout: int) -> dict:
    started_at = datetime.now()
    try:
        proc = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        rc = proc.returncode
        out, err = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        rc = 124
        out = e.stdout or "" if isinstance(e.stdout, str) else ""
        err = (e.stderr or "" if isinstance(e.stderr, str) else "") + f"\n[timeout after {timeout}s]"
    except Exception as e:
        rc = 125
        out = ""
        err = f"[exception in run_step] {e}\n{traceback.format_exc()}"
    finished_at = datetime.now()
    return {
        "name": name,
        "command": command,
        "returncode": rc,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "stdout": out,
        "stderr": err,
    }


def write_log(run_id: str, steps: list[dict]) -> tuple[Path, Path]:
    log_dir = PROJECT_ROOT / "stockbot" / "logs" / "daily"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{run_id}.log"
    summary_path = log_dir / f"{run_id}.json"

    lines: list[str] = []
    for step in steps:
        lines.append(f"===== {step['name']} returncode={step['returncode']} =====")
        lines.append("$ " + " ".join(step["command"]))
        lines.append("--- stdout ---")
        lines.append(step["stdout"] or "")
        lines.append("--- stderr ---")
        lines.append(step["stderr"] or "")
    log_path.write_text("\n".join(lines), encoding="utf-8")

    summary = {
        "run_id": run_id,
        "project_root": str(PROJECT_ROOT),
        "ok": all(step["returncode"] == 0 for step in steps),
        "steps": [
            {
                "name": step["name"],
                "returncode": step["returncode"],
                "started_at": step["started_at"],
                "finished_at": step["finished_at"],
            }
            for step in steps
        ],
        "log_path": str(log_path),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return log_path, summary_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--limit", type=int, default=50, help="universe size")
    parser.add_argument("--top", type=int, default=20, help="top universe symbols to trade")
    parser.add_argument("--skip-universe", action="store_true", help="reuse existing stockbot/data/universe.csv")
    parser.add_argument("--universe-file", default="stockbot/data/universe.csv")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--force", action="store_true", help="bypass is_trade_day check")
    args = parser.parse_args()

    if not PYTHON.exists():
        print(f"missing venv python: {PYTHON}", file=sys.stderr)
        return 2

    # ---- 0. 交易日短路 ----
    if not args.force:
        is_trade_day = _try_import_calendar()
        if is_trade_day is not None and not is_trade_day(date.today()):
            print(f"daily_run=SKIP reason=non_trade_day date={date.today()}")
            return 0

    cfg = _try_load_cfg(args.config)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    steps: list[dict] = []

    def _fail(step_name: str, rc: int):
        log_path, summary_path = write_log(run_id, steps)
        tail = (steps[-1].get("stderr") or steps[-1].get("stdout") or "").strip()
        print(f"daily_run=FAILED step={step_name} log={log_path} summary={summary_path}")
        _push_lark_failure(cfg, run_id, step_name, log_path, tail)
        return rc

    if not args.skip_universe:
        steps.append(
            run_step(
                "build_universe",
                [str(PYTHON), "scripts/build_universe.py", "--config", args.config, "--limit", str(args.limit), "--output", args.universe_file],
                args.timeout,
            )
        )
        if steps[-1]["returncode"] != 0:
            return _fail("build_universe", steps[-1]["returncode"])

    market_context_step = run_step(
        "build_market_context",
        [
            str(PYTHON),
            "scripts/build_market_context.py",
            "--config", args.config,
            "--top", "20",
            "--timeout", "20",
            "--allow-fallback",
            "--min-refresh-time", "11:00",
        ],
        min(args.timeout, 120),
    )
    steps.append(market_context_step)
    if market_context_step["returncode"] != 0:
        return _fail("build_market_context", market_context_step["returncode"])

    dragon_pool_step = run_step(
        "build_dragon_pool",
        [
            str(PYTHON),
            "scripts/build_dragon_pool.py",
            "--config", args.config,
            "--universe-file", args.universe_file,
            "--top", str(args.top),
            "--allow-fallback",
        ],
        min(args.timeout, 180),
    )
    steps.append(dragon_pool_step)
    # build_dragon_pool 在池为空时可能返回 3 而非 0,这里做硬熔断
    if dragon_pool_step["returncode"] != 0:
        return _fail("build_dragon_pool", dragon_pool_step["returncode"])

    steps.append(
        run_step(
            "run_trader",
            [str(PYTHON), "scripts/run_trader.py", "--config", args.config, "--universe-file", args.universe_file, "--top", str(args.top)],
            args.timeout,
        )
    )
    if steps[-1]["returncode"] != 0:
        return _fail("run_trader", steps[-1]["returncode"])

    steps.append(
        run_step(
            "generate_daily_report",
            [str(PYTHON), "scripts/generate_daily_report.py", "--config", args.config],
            args.timeout,
        )
    )
    if steps[-1]["returncode"] != 0:
        return _fail("generate_daily_report", steps[-1]["returncode"])

    log_path, summary_path = write_log(run_id, steps)
    ok = all(step["returncode"] == 0 for step in steps)
    print(f"daily_run={'OK' if ok else 'FAILED'} log={log_path} summary={summary_path}")
    for step in steps:
        print(f"{step['name']} returncode={step['returncode']}")
        tail = (step["stdout"] or step["stderr"] or "").strip().splitlines()[-8:]
        for line in tail:
            print(line)

    if ok:
        # 成功也推一条简短通知,确认调度真的在跑
        _push_lark_info(cfg, f"✅ stockbot daily_run OK run_id={run_id}")
    else:
        _push_lark_failure(cfg, run_id, "post_check", log_path, "see log")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
