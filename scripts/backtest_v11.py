"""v1.1 回测对比:同一段历史数据,分别在 v1.0(关闭 dragon_head_exit)和 v1.1(开启)下跑一遍。

输出:
- stockbot/backtests/v11_compare/v10/
- stockbot/backtests/v11_compare/v11/
- stockbot/backtests/v11_compare/compare.md  对比汇总

用法:
  .venv/bin/python scripts/backtest_v11.py --top 30 --count 60
默认从 stockbot/data/universe.csv 取前 30 只,回测 60 个交易日。
"""
from __future__ import annotations

import argparse
import copy
import json
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
        path = Path(args.universe_file)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        df = pd.read_csv(path)
        return df["symbol"].head(args.top).astype(str).tolist()
    raise SystemExit("必须传 --symbols 或 --universe-file")


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def _diff_table(v10: dict, v11: dict) -> str:
    keys = ["initial_asset", "final_asset", "total_return", "max_drawdown",
            "annualized_sharpe", "trade_count", "buy_count", "sell_count",
            "closed_rounds", "win_rate", "avg_closed_return"]
    lines = ["| 指标 | v1.0(无龙头卖出) | v1.1(龙头卖出) | 差值 |", "|---|---:|---:|---:|"]
    for k in keys:
        a = v10.get(k, "-")
        b = v11.get(k, "-")
        try:
            d = float(b) - float(a)
            d_str = f"{d:+.4f}" if isinstance(d, float) else str(d)
        except Exception:
            d_str = "-"
        lines.append(f"| {k} | {_fmt(a)} | {_fmt(b)} | {d_str} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--universe-file", default="stockbot/data/universe.csv")
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--count", type=int, default=120)
    parser.add_argument("--output", default="stockbot/backtests/v11_compare")
    args = parser.parse_args()

    cfg = load_config(args.config)
    # 弱网防卡:每只最多 2 次 + 每次 0.5s 间隔,失败直接 skip
    # 重试 5 次,初始 0.8s 间隔(成倍增长在 akshare adapter 里实现)
    cfg.setdefault("market_data", {})["retries"] = 5
    cfg["market_data"]["retry_sleep"] = 0.8
    # 单次请求 socket 超时 10s,卡住立刻进重试而不是阻塞 30s+
    import socket
    socket.setdefaulttimeout(10)
    symbols = load_symbols(args)
    print(f"[backtest_v11] symbols={len(symbols)} count={args.count}")

    print(f"[backtest_v11] fetching history...")
    bars = AkshareMarketData(cfg).get_history(AkshareHistoryRequest(symbols=symbols, count=args.count))
    print(f"[backtest_v11] bars_loaded={len(bars)}")

    output_root = Path(args.output)
    if not output_root.is_absolute():
        output_root = PROJECT_ROOT / output_root
    output_root.mkdir(parents=True, exist_ok=True)

    # ---- v1.0: 关闭 dragon_head_exit
    cfg_v10 = copy.deepcopy(cfg)
    cfg_v10.setdefault("dragon_head_exit", {})["enabled"] = False
    print(f"[backtest_v11] running v1.0...")
    res_v10 = Backtester(cfg_v10).run(bars)
    paths_v10 = Backtester(cfg_v10).save(res_v10, output_root / "v10")
    print(f"[backtest_v11] v1.0 done; trades={res_v10.metrics.get('trade_count')}")

    # ---- v1.1: 开启 dragon_head_exit
    cfg_v11 = copy.deepcopy(cfg)
    cfg_v11.setdefault("dragon_head_exit", {})["enabled"] = True
    print(f"[backtest_v11] running v1.1...")
    res_v11 = Backtester(cfg_v11).run(bars)
    paths_v11 = Backtester(cfg_v11).save(res_v11, output_root / "v11")
    print(f"[backtest_v11] v1.1 done; trades={res_v11.metrics.get('trade_count')}")

    # ---- compare.md
    md_lines = [
        "# v1.0 vs v1.1 回测对比",
        "",
        f"- 标的数: {len(symbols)}",
        f"- 历史长度: {args.count} 个交易日",
        f"- bars_loaded: {len(bars)}",
        "",
        "## 指标对比",
        "",
        _diff_table(res_v10.metrics, res_v11.metrics),
        "",
        "## v1.1 卖出原因分布",
        "",
    ]
    if not res_v11.trades.empty:
        sells = res_v11.trades[res_v11.trades["side"] == "SELL"]
        if not sells.empty:
            counts = sells["reason"].value_counts()
            md_lines.append("| 卖点 | 次数 |")
            md_lines.append("|---|---:|")
            for reason, cnt in counts.items():
                md_lines.append(f"| {reason} | {cnt} |")
        else:
            md_lines.append("(无卖出)")
    else:
        md_lines.append("(无交易)")

    md_lines += [
        "",
        "## 文件",
        f"- v1.0 equity: {paths_v10['equity']}",
        f"- v1.0 trades: {paths_v10['trades']}",
        f"- v1.1 equity: {paths_v11['equity']}",
        f"- v1.1 trades: {paths_v11['trades']}",
        "",
    ]
    compare_path = output_root / "compare.md"
    compare_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"[backtest_v11] compare written to {compare_path}")

    # 同时打印关键 diff 到 stdout
    print()
    print("=" * 60)
    print(_diff_table(res_v10.metrics, res_v11.metrics))
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
