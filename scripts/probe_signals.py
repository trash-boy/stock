"""探针:跑一遍 strategy + _cannot_buy,统计为什么 trade_count=0。"""
from __future__ import annotations
import sys, copy, socket
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stockbot.adapters.akshare_market import AkshareHistoryRequest, AkshareMarketData
from stockbot.core.backtest import Backtester
from stockbot.core.config import load_config
from stockbot.strategies.dragon_head import DragonHeadStrategy
import pandas as pd

cfg = load_config(str(ROOT / "config.yaml"))
cfg.setdefault("market_data", {})["retries"] = 5
cfg["market_data"]["retry_sleep"] = 0.8
socket.setdefaulttimeout(10)

uni = pd.read_csv(ROOT / "stockbot/data/universe.csv")
symbols = uni["symbol"].head(30).astype(str).tolist()
print(f"[probe] symbols={len(symbols)}")
bars = AkshareMarketData(cfg).get_history(AkshareHistoryRequest(symbols=symbols, count=240))
print(f"[probe] bars_loaded={len(bars)}")

prepared = {}
for s, df in bars.items():
    if df is None or len(df) < 80:
        continue
    data = df.copy()
    data["date"] = pd.to_datetime(data["date"])
    data = data.sort_values("date").set_index("date")
    prepared[s] = data
print(f"[probe] prepared symbols={len(prepared)}")
dates = sorted(set().union(*[set(df.index) for df in prepared.values()]))
print(f"[probe] dates total={len(dates)}; loop iterations = {max(0, len(dates)-61)}")

cfg_v10 = copy.deepcopy(cfg)
cfg_v10.setdefault("dragon_head_exit", {})["enabled"] = False
strategy = DragonHeadStrategy(cfg_v10)
bt = Backtester(cfg_v10)

signal_total = 0
nonempty_days = 0
cannot_buy_total = 0
cannot_buy_breakdown = {}
sample_signals = []
pool_sizes = []

for idx in range(61, len(dates)):
    signal_date = dates[idx - 1]
    exec_date = dates[idx]
    signal_bars = {s: df.loc[:signal_date].tail(120) for s, df in prepared.items() if signal_date in df.index and len(df.loc[:signal_date]) >= 20}
    exec_rows = {s: df.loc[exec_date] for s, df in prepared.items() if exec_date in df.index}
    if not bool(cfg_v10.get("backtest", {}).get("use_static_dragon_pool", False)):
        pool = bt._point_in_time_dragon_pool(signal_bars)
        strategy.set_runtime_pool(pool)
        pool_sizes.append(len(pool) if hasattr(pool, "__len__") else 0)
    sigs = strategy.generate(signal_bars)
    if sigs:
        nonempty_days += 1
        signal_total += len(sigs)
        if len(sample_signals) < 5:
            sample_signals.append((str(exec_date.date()), [(g.symbol, g.reason) for g in sigs[:3]]))
    for sg in sigs:
        if sg.symbol not in exec_rows:
            continue
        row = exec_rows[sg.symbol]
        why = bt._cannot_buy(row)
        if why:
            cannot_buy_total += 1
            key = str(why)[:80]
            cannot_buy_breakdown[key] = cannot_buy_breakdown.get(key, 0) + 1

print(f"[probe] dragon_pool sizes: min={min(pool_sizes) if pool_sizes else '-'}, max={max(pool_sizes) if pool_sizes else '-'}, mean={sum(pool_sizes)/len(pool_sizes) if pool_sizes else 0:.1f}")
print(f"[probe] strategy generated {signal_total} signals across {nonempty_days}/{max(0,len(dates)-61)} signal-days")
print(f"[probe] _cannot_buy rejected {cannot_buy_total}")
if cannot_buy_breakdown:
    print("[probe] reject reasons:")
    for k, v in sorted(cannot_buy_breakdown.items(), key=lambda x: -x[1]):
        print(f"  {v}x {k}")
print("[probe] sample signals:")
for d, ss in sample_signals:
    print(f"  {d}: {ss}")
