"""Check xtquant market data availability."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stockbot.adapters.xtquant_market import HistoryRequest, XtQuantMarketData
from stockbot.core.config import load_config
from stockbot.strategies.aggressive_growth import AggressiveGrowthStrategy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--symbols", default="300750.SZ,688981.SH,000001.SZ")
    parser.add_argument("--count", type=int, default=120)
    args = parser.parse_args()

    cfg = load_config(args.config)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    market = XtQuantMarketData(cfg)
    try:
        bars = market.get_history(HistoryRequest(symbols=symbols, count=args.count))
    except RuntimeError as exc:
        raise SystemExit(f"行情读取失败：{exc}\n请先运行：python3 scripts/diagnose_xtquant.py") from exc
    print(f"bars_loaded={len(bars)}")
    for symbol, df in bars.items():
        latest = df.iloc[-1]
        print(f"{symbol} close={latest.get('close')} ma20={latest.get('ma20')} ma60={latest.get('ma60')} volume={latest.get('volume')}")
    signals = AggressiveGrowthStrategy(cfg).generate(bars)
    print(f"signals={len(signals)}")
    for signal in signals[:10]:
        print(f"{signal.side.value} {signal.symbol} price={signal.price} weight={signal.weight} reason={signal.reason}")


if __name__ == "__main__":
    main()
