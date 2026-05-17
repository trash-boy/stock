"""Build A-share trading universe from AkShare spot data."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stockbot.adapters.akshare_market import AkshareMarketData
from stockbot.core.config import load_config
from stockbot.core.universe import AShareUniverseFilter


def get_spot_with_retry(market: AkshareMarketData):
    last_error = None
    for attempt in range(3):
        try:
            return market.get_spot()
        except Exception as exc:
            last_error = exc
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"AkShare 全市场行情获取失败: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--limit", type=int, default=0, help="override universe_filter.top_n")
    parser.add_argument("--output", default="", help="override universe_filter.output_path")
    args = parser.parse_args()

    cfg = load_config(args.config)
    rules = cfg.get("universe_filter", {})
    limit = args.limit or int(rules.get("top_n", 50))
    output = args.output or rules.get("output_path", "stockbot/data/universe.csv")

    market = AkshareMarketData(cfg)
    spot = get_spot_with_retry(market)
    result = AShareUniverseFilter(cfg).filter_spot(spot, limit=limit)
    AShareUniverseFilter(cfg).save(result, output)

    print(f"universe_size={len(result.symbols)} output={output}")
    for _, row in result.rows.head(20).iterrows():
        print(
            f"{row['symbol']} {row['name']} price={row['price']} pct={row['pct_change']} "
            f"amount={int(row['amount'])} turnover={row['turnover']} score={round(row['score'], 4)}"
        )


if __name__ == "__main__":
    main()
