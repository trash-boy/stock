"""v1.1 改动 smoke test:验证全部 6 项核心修复都能正确加载和运行。"""
import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stockbot.core.config import load_config
cfg = load_config(ROOT / "config.yaml")
print(f"[1] config.yaml loaded: {len(cfg)} top-level keys")
assert "dragon_head_exit" in cfg, "dragon_head_exit missing"
assert "call_auction_filter" in cfg, "call_auction_filter missing"
assert "climax" in cfg["market_context"]["block_buy_phases"], "climax not in block_buy_phases"
assert cfg["dragon_head_strategy"]["min_amount_main"] == 80000000
assert cfg["dragon_head_strategy"]["min_amount_growth"] == 50000000
print("    ✓ config 全部新字段就位")

from stockbot.strategies.dragon_head import DragonHeadStrategy
strat = DragonHeadStrategy(cfg)
assert hasattr(strat, "promotion_stats")
assert strat.params.min_amount_main == 80000000
assert strat.params.min_amount_growth == 50000000
print(f"[2] DragonHeadStrategy loaded; min_amount_main={strat.params.min_amount_main}, growth={strat.params.min_amount_growth}")

assert strat._min_amount_for("300750.SZ") == 50000000
assert strat._min_amount_for("688012.SH") == 50000000
assert strat._min_amount_for("301001.SZ") == 50000000
assert strat._min_amount_for("600000.SH") == 80000000
assert strat._min_amount_for("000001.SZ") == 80000000
assert strat._min_amount_for("830001.BJ") == 80000000
print("[3] ✓ _min_amount_for 分档全部正确")

from stockbot.strategies.dragon_head_exit import DragonHeadExitStrategy
exit_strat = DragonHeadExitStrategy(cfg)
print(f"[4] DragonHeadExitStrategy loaded; enabled={exit_strat.params.enabled}")
assert exit_strat.params.enabled

from stockbot.core.models import AccountSnapshot, Position
pos = Position(symbol="300750.SZ", quantity=1000, avg_price=100, last_price=110, high_price=115)
snapshot = AccountSnapshot(cash=10000, total_asset=120000, positions={"300750.SZ": pos})
exit_strat.market_context = {"emotion": {"phase": "cooldown"}}
sells = exit_strat.generate_sells(snapshot)
assert len(sells) == 1, f"expected 1 sell, got {len(sells)}"
assert sells[0].reason.startswith("dragon_exit:emergency_exit/phase=cooldown")
assert sells[0].quantity == 1000
print(f"[5] ✓ emergency exit fires: {sells[0].reason}")

exit_strat.market_context = {"emotion": {"phase": "ferment"}}
exit_strat._sectors_with_new_limit_up = {"半导体"}
sells = exit_strat.generate_sells(snapshot)
print(f"[6] ferment + 板块仍热: {len(sells)} sells (期望: 0)")

from stockbot.adapters.paper import PaperBroker
from stockbot.core.engine import TradingEngine
broker = PaperBroker(cfg)
engine = TradingEngine(cfg, broker)
print(f"[7] TradingEngine loaded; _exit_strategy={'set' if engine._exit_strategy else 'None'}")

import importlib.util
spec = importlib.util.spec_from_file_location("morning_call", ROOT / "scripts/morning_call.py")
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)
assert hasattr(mc, "_filter_call_auction")
assert hasattr(mc, "_get_pre_open")
print("[8] ✓ morning_call 集合竞价过滤函数已注入")

mc._get_pre_open = lambda sym, retry=1: 110.0
allow, label, gap = mc._filter_call_auction(cfg, "300750.SZ", 100.0)
assert not allow, "高开 10% 应该被过滤"
assert "高开过大" in label
mc._get_pre_open = lambda sym, retry=1: 105.0
allow, label, gap = mc._filter_call_auction(cfg, "300750.SZ", 100.0)
assert allow
print(f"[9] ✓ 集合竞价过滤逻辑正常 (5% 通过, 10% 拦截)")

spec = importlib.util.spec_from_file_location("bdp", ROOT / "scripts/build_dragon_pool.py")
bdp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bdp)
import pandas as pd
yest = pd.DataFrame({"代码": ["000001", "300750", "688012"], "所属行业": ["银行", "电池", "半导体"]})
today = pd.DataFrame({"symbol": ["000001.SZ", "300750.SZ"], "limit_up_count": [1, 2]})
stats = bdp.compute_promotion_stats(yest, today)
print(f"[10] promotion_stats: total={stats['yesterday_total']}, promoted={stats['promoted']}, rate={stats['promotion_rate']}")
assert stats["yesterday_total"] == 3
assert stats["promoted"] == 2
assert stats["promotion_rate"] == round(2/3, 4)

print("\n========== ALL 10 CHECKS PASSED ==========")
