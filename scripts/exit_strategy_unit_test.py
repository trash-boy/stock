"""单元级验证 DragonHeadExitStrategy 的 4 种卖点都能正确触发。"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timedelta

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from stockbot.core.config import load_config
from stockbot.core.models import AccountSnapshot, Position
from stockbot.strategies.dragon_head_exit import DragonHeadExitStrategy


def make_bars(symbol, sector, last_pct, vol_mult=1.0, days=10):
    rng = np.random.default_rng(42)
    rows = []
    close = 10.0
    for i in range(days - 1):
        pct = float(rng.uniform(-1, 1))
        new_close = close * (1 + pct / 100)
        rows.append({
            "date": (datetime(2026,5,1) + timedelta(days=i)).strftime("%Y-%m-%d"),
            "symbol": symbol, "sector": sector,
            "open": round(close, 2), "close": round(new_close, 2),
            "high": round(new_close*1.005, 2), "low": round(close*0.995, 2),
            "volume": int(1e7), "amount": float(1e8),
            "pct_change": round(pct, 2),
        })
        close = new_close
    new_close = close * (1 + last_pct/100)
    rows.append({
        "date": (datetime(2026,5,1)+timedelta(days=days-1)).strftime("%Y-%m-%d"),
        "symbol": symbol, "sector": sector,
        "open": round(close, 2), "close": round(new_close, 2),
        "high": round(close*1.10, 2), "low": round(close*0.95, 2),
        "volume": int(1e7*vol_mult), "amount": float(1e8*vol_mult),
        "pct_change": round(last_pct, 2),
    })
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def make_account(symbol, qty=1000, cost=9.0, last=10.0):
    pos = Position(symbol=symbol, quantity=qty, avg_price=cost, last_price=last, high_price=last)
    return AccountSnapshot(cash=50000.0, total_asset=100000.0, positions={symbol: pos})


def main():
    cfg = load_config("config.yaml")
    cfg.setdefault("dragon_head_exit", {})["enabled"] = True

    print("=" * 60)
    print("DragonHeadExitStrategy 单元验证")
    print("=" * 60)

    results = []
    SYM = "600999.SH"  # 主板, limit_pct=9.5

    # --- 1. emergency_exit @ cooldown ---
    s = DragonHeadExitStrategy(cfg)
    s.market_context = {"emotion": {"phase": "cooldown"}}
    sells = s.generate_sells(make_account(SYM), {SYM: make_bars(SYM, "AI芯片", -2.0)})
    ok = len(sells) == 1 and "emergency_exit" in sells[0].reason
    results.append(("emergency_exit @ cooldown", ok, sells))

    # --- 2. break_board (open_times>=1, pct < 9.0) ---
    s = DragonHeadExitStrategy(cfg)
    s.market_context = {"emotion": {"phase": "ferment"}}
    s.dragon_pool = pd.DataFrame([{
        "symbol": SYM, "sector": "AI芯片", "open_times": 2, "pct_change": 5.0,
        "limit_up_count": 1,
    }])
    sells = s.generate_sells(make_account(SYM), {SYM: make_bars(SYM, "AI芯片", 5.0)})
    ok = any("break_board" in o.reason for o in sells)
    results.append(("break_board_exit", ok, sells))

    # --- 3. theme_dead (sector无新涨停 + 自身pct远离涨停价) ---
    s = DragonHeadExitStrategy(cfg)
    s.market_context = {"emotion": {"phase": "ferment"}}
    s.dragon_pool = pd.DataFrame([{
        "symbol": SYM, "sector": "冷门题材", "open_times": 0,
        "pct_change": -1.0, "limit_up_count": 0,
    }])
    s._sectors_with_new_limit_up = set()  # 板块当日无新涨停
    sells = s.generate_sells(make_account(SYM), {SYM: make_bars(SYM, "冷门题材", -1.0)})
    ok = any("theme_dead" in o.reason for o in sells)
    results.append(("theme_dead_exit", ok, sells))

    # --- 4. momentum_failure (量比1.5+, pct<3) ---
    s = DragonHeadExitStrategy(cfg)
    s.market_context = {"emotion": {"phase": "ferment"}}
    s.dragon_pool = pd.DataFrame([{
        "symbol": SYM, "sector": "AI芯片", "open_times": 0,
        "pct_change": 1.0, "limit_up_count": 0,
    }])
    s._sectors_with_new_limit_up = {"AI芯片"}  # 排除 theme_dead
    sells = s.generate_sells(make_account(SYM), {SYM: make_bars(SYM, "AI芯片", 1.0, vol_mult=2.5)})
    ok = any("momentum_failure" in o.reason for o in sells)
    results.append(("momentum_failure_exit", ok, sells))

    # --- 5. negative: ferment + 板上 → 不卖 ---
    s = DragonHeadExitStrategy(cfg)
    s.market_context = {"emotion": {"phase": "ferment"}}
    s.dragon_pool = pd.DataFrame([{
        "symbol": SYM, "sector": "AI芯片", "open_times": 0,
        "pct_change": 9.95, "limit_up_count": 1,
    }])
    s._sectors_with_new_limit_up = {"AI芯片"}
    sells = s.generate_sells(make_account(SYM, last=11.0),
                             {SYM: make_bars(SYM, "AI芯片", 9.95)})
    ok = len(sells) == 0
    results.append(("no_sell @ ferment+板上", ok, sells))

    print()
    for name, ok, sells in results:
        mark = "✓" if ok else "✗"
        info = [(o.symbol, o.reason, o.quantity) for o in sells]
        print(f"  {mark} {name:35s}  sells={info}")
    n_pass = sum(1 for _, ok, _ in results if ok)
    print(f"\n通过 {n_pass}/{len(results)}")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
