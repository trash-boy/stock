"""v1.1 合成数据回测：构造 ~150 天人造行情验证 dragon_head_exit 钩子触发。"""
from __future__ import annotations

import copy
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stockbot.core.backtest import Backtester
from stockbot.core.config import load_config


def _gen_one(symbol, name, sector, n_days, seed, pattern):
    rng = np.random.default_rng(seed)
    base_date = datetime(2026, 1, 1)
    rows = []
    close = 10.0
    for i in range(n_days):
        action = pattern[i % len(pattern)]
        if action == "lu":
            pct = 9.95
        elif action == "up":
            pct = float(rng.uniform(2, 6))
        elif action == "down":
            pct = float(rng.uniform(-6, -2))
        elif action == "break":
            pct = float(rng.uniform(-3, 1))
        else:
            pct = float(rng.uniform(-1, 1))
        new_close = close * (1 + pct / 100)
        open_ = close * (1 + rng.uniform(-0.005, 0.005))
        high = max(open_, new_close) * (1 + rng.uniform(0, 0.005))
        low = min(open_, new_close) * (1 - rng.uniform(0, 0.005))
        volume = int(rng.uniform(5e6, 2e7))
        amount = volume * (open_ + new_close) / 2
        rows.append({
            "date": (base_date + timedelta(days=i)).strftime("%Y-%m-%d"),
            "symbol": symbol, "name": name, "sector": sector, "industry": sector,
            "open": round(open_, 2), "close": round(new_close, 2),
            "high": round(high, 2), "low": round(low, 2),
            "volume": volume, "amount": round(amount, 2),
            "amplitude": round(abs(high - low) / close * 100, 2),
            "pct_change": round(pct, 2),
            "change": round(new_close - close, 2),
            "turnover": round(rng.uniform(3, 15), 2),
        })
        close = new_close
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    df["volume_ma20"] = df["volume"].rolling(20).mean()
    return df.dropna(subset=["ma20", "ma60", "volume_ma20"]).reset_index(drop=True)


def build_bars():
    # 总长 200 天，前 60 天用于 ma60 预热
    p_dragon = (["flat"]*80 + ["lu","lu","lu","lu"] + ["break"] + ["down","down"] +
                ["flat"]*5 + ["down"]*10 + ["flat"]*100)
    p_follow = (["flat"]*82 + ["lu","lu"] + ["down","down","down"] + ["flat"]*120)
    p_weak = ["down","flat","down","flat","up"]*40
    p_peer = (["flat"]*81 + ["up","lu","up","lu"] + ["down","flat","down"] + ["flat"]*120)
    return {
        "300999.SZ": _gen_one("300999.SZ", "龙头股", "AI芯片", 200, 1, p_dragon),
        "002999.SZ": _gen_one("002999.SZ", "跟风一", "AI芯片", 200, 2, p_follow),
        "600999.SH": _gen_one("600999.SH", "弱势股", "传统行业", 200, 3, p_weak),
        "688999.SH": _gen_one("688999.SH", "同板块", "AI芯片", 200, 4, p_peer),
    }


def _fmt(v):
    return f"{v:.4f}" if isinstance(v, float) else str(v)


def _diff_table(v10, v11):
    keys = ["initial_asset","final_asset","total_return","max_drawdown",
            "trade_count","buy_count","sell_count","closed_rounds","win_rate","avg_closed_return"]
    lines = ["| 指标 | v1.0(无龙头卖出) | v1.1(龙头卖出) | 差值 |", "|---|---:|---:|---:|"]
    for k in keys:
        a, b = v10.get(k, "-"), v11.get(k, "-")
        try:
            d = float(b) - float(a)
            d_str = f"{d:+.4f}"
        except Exception:
            d_str = "-"
        lines.append(f"| {k} | {_fmt(a)} | {_fmt(b)} | {d_str} |")
    return "\n".join(lines)


def main():
    cfg = load_config("config.yaml")
    bars = build_bars()
    sample = next(iter(bars.values()))
    print(f"[synth_bt] generated {len(bars)} symbols, {len(sample)} bars each")

    output_root = PROJECT_ROOT / "stockbot/backtests/v11_compare_synth"
    output_root.mkdir(parents=True, exist_ok=True)

    cfg_v10 = copy.deepcopy(cfg)
    cfg_v10.setdefault("dragon_head_exit", {})["enabled"] = False
    print("[synth_bt] running v1.0...")
    res_v10 = Backtester(cfg_v10).run(bars)
    paths_v10 = Backtester(cfg_v10).save(res_v10, output_root / "v10")
    print(f"[synth_bt] v1.0 metrics: {res_v10.metrics}")

    cfg_v11 = copy.deepcopy(cfg)
    cfg_v11.setdefault("dragon_head_exit", {})["enabled"] = True
    print("[synth_bt] running v1.1...")
    res_v11 = Backtester(cfg_v11).run(bars)
    paths_v11 = Backtester(cfg_v11).save(res_v11, output_root / "v11")
    print(f"[synth_bt] v1.1 metrics: {res_v11.metrics}")

    md = ["# v1.0 vs v1.1 合成数据回测", "",
          "> 注：人造行情，仅用于验证钩子触发。",
          "", "## 指标对比", "",
          _diff_table(res_v10.metrics, res_v11.metrics), "",
          "## 卖出原因分布", ""]
    for label, res in [("v1.1", res_v11), ("v1.0", res_v10)]:
        if not res.trades.empty:
            sells = res.trades[res.trades["side"] == "SELL"]
            if not sells.empty:
                md += [f"### {label}", "| 卖点 | 次数 |", "|---|---:|"]
                for r, c in sells["reason"].value_counts().items():
                    md.append(f"| {r} | {c} |")
                md.append("")
    (output_root / "compare.md").write_text("\n".join(md), encoding="utf-8")
    print()
    print(_diff_table(res_v10.metrics, res_v11.metrics))
    if not res_v11.trades.empty:
        sells = res_v11.trades[res_v11.trades["side"] == "SELL"]
        if not sells.empty:
            print("\nv1.1 sell reasons:")
            print(sells["reason"].value_counts().to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
