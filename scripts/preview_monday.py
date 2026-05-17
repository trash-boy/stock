"""周日预演:基于周五的 dragon_pool + universe,看周一会出什么 BUY 信号 / watch list。

流程:
1. 加载周五的 universe
2. 拉过去 120 天的 bars(走 baostock 兜底)
3. 用 DragonHeadStrategy.generate() 跑一遍
4. 打印:phase / dragon_pool / executable BUY / watch_list_tomorrow / 三档 buy_point 命中的票
"""
from __future__ import annotations
import sys, socket
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from stockbot.adapters.akshare_market import AkshareHistoryRequest, AkshareMarketData
from stockbot.core.config import load_config
from stockbot.strategies.dragon_head import DragonHeadStrategy

cfg = load_config(str(ROOT / "config.yaml"))
cfg.setdefault("market_data", {})["retries"] = 5
cfg["market_data"]["retry_sleep"] = 0.8
socket.setdefaulttimeout(10)

# 用 dragon_pool 里的票 + universe 头部凑齐候选
pool = pd.read_csv(ROOT / "stockbot/data/dragon_pool.csv")
uni = pd.read_csv(ROOT / "stockbot/data/universe.csv")

symbols = list(dict.fromkeys(
    pool["symbol"].astype(str).tolist() + uni["symbol"].astype(str).head(30).tolist()
))[:50]
print(f"[preview] 候选标的数={len(symbols)}")

bars = AkshareMarketData(cfg).get_history(AkshareHistoryRequest(symbols=symbols, count=120))
print(f"[preview] bars_loaded={len(bars)}")

strategy = DragonHeadStrategy(cfg)
phase = strategy._emotion_phase()
print(f"[preview] 当前 phase={phase}")
print(f"[preview] block_buy_phases={cfg.get('market_context', {}).get('block_buy_phases', ['panic','cooldown','unknown'])}")

# 直接打分,看每只标的属于哪种买点
scored = []
for sym, df in bars.items():
    item = strategy.score_one(sym, df)
    if item:
        scored.append(item)
scored.sort(key=lambda x: x["score"], reverse=True)

print(f"\n=== Top 15 dragon_pool 候选(按 score)===")
print(f"{'symbol':12} {'name':10} {'sector':12} {'role':16} {'buy_point':28} {'score':>7} {'pct':>6} {'streak':>3}")
for it in scored[:15]:
    print(f"{it['symbol']:12} {str(it.get('name',''))[:10]:10} {str(it.get('sector',''))[:12]:12} {str(it.get('role',''))[:16]:16} {str(it.get('buy_point',''))[:28]:28} {it['score']:7.4f} {it['pct_change']:6.2f} {it.get('limit_up_count',0):3d}")

# 调 generate 拿真实信号
sigs = strategy.generate(bars)
print(f"\n=== 周一可执行 BUY 信号(strategy.generate)===")
if not sigs:
    print("  (空) — 没有 weak_to_strong / divergence_to_consensus / first_negative_pullback 形态的票")
for s in sigs:
    print(f"  {s.symbol}  weight={s.weight:.2f}  price≈{s.price}  reason={s.reason}")

# 看看 watch_list_tomorrow 写了什么
wl_path = ROOT / "stockbot/data/watch_list_tomorrow.csv"
if wl_path.exists():
    wl = pd.read_csv(wl_path)
    print(f"\n=== 明日观察池(已封板龙头,周一开盘看是否能上车)===")
    if wl.empty:
        print("  (空)")
    else:
        for _, r in wl.iterrows():
            print(f"  {r['symbol']:12} {r['name']:10} {r['sector']:12} role={r['role']:14} buy_point={r['buy_point']:25} 连板={r.get('limit_up_count','?')} 板块第{r.get('sector_rank','?')}")

# 按 buy_point 分布
from collections import Counter
bp_cnt = Counter(it["buy_point"] for it in scored)
print(f"\n=== 候选池 buy_point 分布 ===")
for bp, cnt in bp_cnt.most_common():
    mark = "✅可执行" if bp in {"weak_to_strong","divergence_to_consensus","first_negative_pullback"} else "👀观察"
    print(f"  {mark:10} {bp:30} {cnt}")
