"""Build market emotion cycle and sector heat context."""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stockbot.adapters.akshare_market import AkshareMarketData
from stockbot.core.config import load_config
from stockbot.core.market_context import EmotionCycleAnalyzer, SectorHeatAnalyzer, EmotionSnapshot


def _project_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _worker(method_name: str, cfg: dict, queue: mp.Queue) -> None:
    try:
        market = AkshareMarketData(cfg)
        queue.put((True, getattr(market, method_name)()))
    except Exception as exc:
        queue.put((False, repr(exc)))


def call_with_timeout(name: str, method_name: str, cfg: dict, timeout_sec: int):
    queue: mp.Queue = mp.Queue(maxsize=1)
    proc = mp.Process(target=_worker, args=(method_name, cfg, queue), daemon=True)
    proc.start()
    proc.join(timeout_sec)
    if proc.is_alive():
        proc.terminate()
        proc.join(3)
        print(f"warn: {name} timeout after {timeout_sec}s")
        return None
    if queue.empty():
        print(f"warn: {name} returned no data")
        return None
    ok, payload = queue.get()
    if not ok:
        print(f"warn: {name} error: {payload}")
        return None
    return payload


def retry(name: str, method_name: str, cfg: dict, timeout_sec: int):
    for attempt in range(2):
        data = call_with_timeout(name, method_name, cfg, timeout_sec)
        if data is not None:
            return data
        time.sleep(1.0 * (attempt + 1))
    return None


def _before_refresh_time(time_text: str) -> bool:
    if not time_text:
        return False
    target = datetime.strptime(time_text, "%H:%M").time()
    return datetime.now().time() < target


def _has_valid_existing_context(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    emotion = payload.get("emotion", {}) or {}
    return emotion.get("phase") not in {None, "", "unknown"} and int(emotion.get("total", 0) or 0) > 0


def _write_unknown_context(output: str, reason: str) -> Path:
    path = _project_path(output)
    if _has_valid_existing_context(path):
        print(f"warn: keep_existing_market_context=true reason={reason} path={path}")
        return path
    heat = SectorHeatAnalyzer({})
    emotion = EmotionSnapshot("unknown", 0.0, 0, 0, 0, 0, 0, 0, 0, 0.0)
    return heat.save_context(emotion, [], [], str(path))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--output", default="stockbot/data/market_context.json")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--allow-fallback", action="store_true")
    parser.add_argument("--min-refresh-time", default="11:00", help="avoid early-session false panic before this HH:MM; use --force to override")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    output_path = _project_path(args.output)
    if not args.force and _before_refresh_time(args.min_refresh_time):
        if output_path.exists():
            print(f"market_context={output_path}")
            print(f"skip_refresh=true reason=before_{args.min_refresh_time}_reuse_existing")
            return
        if args.allow_fallback:
            path = _write_unknown_context(str(output_path), f"before_{args.min_refresh_time}_no_existing")
            print(f"market_context={path}")
            print(f"emotion phase=unknown score=0.0 fallback=true reason=before_{args.min_refresh_time}")
            return
        raise SystemExit(f"当前早于 {args.min_refresh_time}，不刷新 market_context，避免早盘误判 panic")

    cfg = load_config(args.config)
    spot = retry("spot", "get_spot", cfg, args.timeout)
    heat = SectorHeatAnalyzer(cfg)
    if spot is None:
        if not args.allow_fallback:
            raise SystemExit("无法获取全市场行情，无法判断情绪周期")
        path = _write_unknown_context(str(output_path), "spot_unavailable")
        print(f"market_context={path}")
        if _has_valid_existing_context(path):
            print("fallback=true action=keep_existing_context")
        else:
            print("emotion phase=unknown score=0.0 fallback=true")
        return

    emotion = EmotionCycleAnalyzer().analyze_spot(spot)
    concept = retry("concept_boards", "get_concept_boards", cfg, args.timeout)
    industry = retry("industry_boards", "get_industry_boards", cfg, args.timeout)
    concept_heat = heat.rank_boards(concept, args.top) if concept is not None else []
    industry_heat = heat.rank_boards(industry, args.top) if industry is not None else []
    path = heat.save_context(emotion, concept_heat, industry_heat, str(output_path))

    print(f"market_context={path}")
    print(f"emotion phase={emotion.phase} score={emotion.score} up={emotion.up_count}/{emotion.total} limit_up={emotion.limit_up_count} limit_down={emotion.limit_down_count} median={emotion.median_pct_change}")
    print("top_concepts=")
    for row in concept_heat[:10]:
        print(f"  {row['name']} pct={row['pct_change']} up={int(row['up_count'])} amount={int(row['amount'])} leader={row['leader_stock']} score={round(row['heat_score'],4)}")
    print("top_industries=")
    for row in industry_heat[:10]:
        print(f"  {row['name']} pct={row['pct_change']} up={int(row['up_count'])} amount={int(row['amount'])} leader={row['leader_stock']} score={round(row['heat_score'],4)}")


if __name__ == "__main__":
    main()
