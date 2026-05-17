"""Run leader-strategy backtest using AkShare history."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stockbot.adapters.akshare_market import AkshareHistoryRequest, AkshareMarketData
from stockbot.core.backtest import Backtester
from stockbot.core.config import load_config


def load_symbols(args) -> list[str]:
    if args.symbols:
        return [s.strip() for s in args.symbols.split(",") if s.strip()]
    if args.universe_file:
        import pandas as pd
        df = pd.read_csv(args.universe_file)
        return df["symbol"].head(args.top).astype(str).tolist()
    raise SystemExit("必须传 --symbols 或 --universe-file")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--universe-file", default="stockbot/data/universe.csv")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--count", type=int, default=260)
    parser.add_argument("--output", default="stockbot/backtests/latest")
    args = parser.parse_args()

    cfg = load_config(args.config)
    symbols = load_symbols(args)
    print(f"loading_history symbols={len(symbols)} count={args.count}")
    bars = AkshareMarketData(cfg).get_history(AkshareHistoryRequest(symbols=symbols, count=args.count))
    print(f"bars_loaded={len(bars)}")
    result = Backtester(cfg).run(bars)
    paths = Backtester(cfg).save(result, args.output)
    print("metrics=")
    for key, value in result.metrics.items():
        print(f"  {key}: {value}")
    print("outputs=")
    for key, value in paths.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
