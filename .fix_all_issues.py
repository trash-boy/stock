from pathlib import Path

root = Path('/Users/bytedance/PycharmProjects/stock')

# 1) models.py: add high_price + trailing helper.
models = root / 'stockbot/core/models.py'
models.write_text('''"""Core domain models."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Signal:
    symbol: str
    side: Side
    weight: float
    reason: str
    price: Optional[float] = None


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: Side
    quantity: int
    price: float
    reason: str


@dataclass
class Position:
    symbol: str
    quantity: int
    avg_price: float
    last_price: float
    high_price: float | None = None

    @property
    def market_value(self) -> float:
        return self.quantity * self.last_price

    @property
    def pnl_pct(self) -> float:
        if self.avg_price <= 0:
            return 0.0
        return self.last_price / self.avg_price - 1.0

    @property
    def drawdown_from_high_pct(self) -> float:
        high = self.high_price if self.high_price is not None else self.last_price
        if high <= 0:
            return 0.0
        return self.last_price / high - 1.0


@dataclass
class AccountSnapshot:
    cash: float
    total_asset: float
    positions: dict[str, Position]
    daily_pnl_pct: float = 0.0
''', encoding='utf-8')

# 2) risk.py: implement trailing stop.
risk = root / 'stockbot/core/risk.py'
risk.write_text('''"""Risk manager: every live order must pass this gate."""
from __future__ import annotations

from datetime import datetime

from stockbot.core.models import AccountSnapshot, OrderIntent, Side


class RiskReject(Exception):
    pass


class RiskManager:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.risk = cfg["risk"]
        self.execution = cfg["execution"]

    def validate_runtime(self) -> None:
        runtime = self.cfg.get("runtime", {})
        if runtime.get("env") == "live" and runtime.get("enabled") is not True:
            raise RiskReject("实盘模式未启用：请在 config.yaml 设置 runtime.enabled=true")

    def validate_order(self, account: AccountSnapshot, order: OrderIntent, today_buy_amount: float) -> None:
        self.validate_runtime()
        if order.quantity <= 0:
            raise RiskReject("订单数量必须大于 0")
        if order.quantity % int(self.execution.get("lot_size", 100)) != 0:
            raise RiskReject("A股订单数量必须是 100 股整数倍")
        amount = order.quantity * order.price
        if order.side == Side.BUY:
            self._validate_buy(account, order, amount, today_buy_amount)
        else:
            self._validate_sell(account, order)

    def _validate_buy(self, account: AccountSnapshot, order: OrderIntent, amount: float, today_buy_amount: float) -> None:
        now = datetime.now().time()
        block_after = datetime.strptime(self.risk["block_new_buy_after"], "%H:%M:%S").time()
        if now >= block_after:
            raise RiskReject("已过新开买入截止时间")
        if account.daily_pnl_pct <= -float(self.risk["max_daily_loss_pct"]):
            raise RiskReject("触发单日亏损熔断，禁止买入")
        if amount > float(self.risk["max_single_order_amount"]):
            raise RiskReject("超过单笔订单金额上限")
        if today_buy_amount + amount > float(self.risk["max_daily_buy_amount"]):
            raise RiskReject("超过单日买入金额上限")
        if len(account.positions) >= int(self.risk["max_positions"]) and order.symbol not in account.positions:
            raise RiskReject("超过最大持仓数量")
        if amount > account.cash:
            raise RiskReject("现金不足")
        min_cash = account.total_asset * float(self.risk["min_cash_reserve_pct"])
        if account.cash - amount < min_cash:
            raise RiskReject("买入后现金低于最低保留比例")
        total_position = sum(p.market_value for p in account.positions.values()) + amount
        if account.total_asset > 0 and total_position / account.total_asset > float(self.risk["max_total_position_pct"]):
            raise RiskReject("超过总仓位上限")
        current_value = account.positions.get(order.symbol).market_value if order.symbol in account.positions else 0.0
        if account.total_asset > 0 and (current_value + amount) / account.total_asset > float(self.risk["max_single_position_pct"]):
            raise RiskReject("超过单票仓位上限")

    def _validate_sell(self, account: AccountSnapshot, order: OrderIntent) -> None:
        pos = account.positions.get(order.symbol)
        if pos is None or pos.quantity < order.quantity:
            raise RiskReject("卖出数量超过当前持仓")

    def stop_orders(self, account: AccountSnapshot) -> list[OrderIntent]:
        orders: list[OrderIntent] = []
        stop_loss = float(self.risk["stop_loss_pct"])
        take_profit = float(self.risk["take_profit_pct"])
        trailing_stop = float(self.risk.get("trailing_stop_pct", 0) or 0)
        for pos in account.positions.values():
            if pos.last_price <= 0 or pos.quantity <= 0:
                continue
            if pos.pnl_pct <= -stop_loss:
                orders.append(OrderIntent(pos.symbol, Side.SELL, pos.quantity, pos.last_price, "stop_loss"))
            elif pos.pnl_pct >= take_profit:
                orders.append(OrderIntent(pos.symbol, Side.SELL, pos.quantity, pos.last_price, "take_profit"))
            elif trailing_stop > 0 and pos.high_price is not None and pos.high_price > pos.avg_price and pos.drawdown_from_high_pct <= -trailing_stop:
                orders.append(OrderIntent(pos.symbol, Side.SELL, pos.quantity, pos.last_price, "trailing_stop"))
        return orders
''', encoding='utf-8')

# 3) paper.py: persist high-water mark and resolve relative state path by project root.
paper = root / 'stockbot/adapters/paper.py'
paper.write_text('''"""Paper broker for local simulation.

It persists a simple cash/position ledger to JSON and executes orders at the
price passed by the strategy. This is only for validating strategy + risk logic.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stockbot.core.models import AccountSnapshot, OrderIntent, Position, Side

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _project_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else PROJECT_ROOT / path


class PaperBroker:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        paper_cfg = cfg.get("paper", {})
        self.state_path = _project_path(paper_cfg.get("state_path", "stockbot/data/paper_account.json"))
        self.initial_cash = float(paper_cfg.get("initial_cash", 100000))
        self._state = self._load_state()
        self._migrate_state()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"cash": self.initial_cash, "positions": {}, "orders": []}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _migrate_state(self) -> None:
        changed = False
        for pos in self._state.setdefault("positions", {}).values():
            avg = float(pos.get("avg_price", 0) or 0)
            last = float(pos.get("last_price", avg) or avg)
            if "high_price" not in pos:
                pos["high_price"] = max(avg, last)
                changed = True
        if changed:
            self._save_state()

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")

    def snapshot(self) -> AccountSnapshot:
        positions: dict[str, Position] = {}
        for symbol, raw in self._state.get("positions", {}).items():
            qty = int(raw.get("quantity", 0))
            if qty <= 0:
                continue
            avg_price = float(raw.get("avg_price", 0) or 0)
            last_price = float(raw.get("last_price", avg_price) or avg_price)
            high_price = float(raw.get("high_price", max(avg_price, last_price)) or max(avg_price, last_price))
            positions[symbol] = Position(symbol, qty, avg_price, last_price, high_price)
        cash = float(self._state.get("cash", 0))
        total_asset = cash + sum(p.market_value for p in positions.values())
        return AccountSnapshot(cash=cash, total_asset=total_asset, positions=positions)

    def mark_price(self, symbol: str, price: float) -> None:
        if price <= 0:
            return
        pos = self._state.setdefault("positions", {}).get(symbol)
        if pos:
            avg = float(pos.get("avg_price", price) or price)
            prev_high = float(pos.get("high_price", max(avg, price)) or max(avg, price))
            pos["last_price"] = float(price)
            pos["high_price"] = max(prev_high, float(price))
            self._save_state()

    def place_order(self, order: OrderIntent) -> int:
        if order.side == Side.BUY:
            self._buy(order)
        else:
            self._sell(order)
        self._state.setdefault("orders", []).append(
            {
                "symbol": order.symbol,
                "side": order.side.value,
                "quantity": order.quantity,
                "price": order.price,
                "amount": round(order.quantity * order.price, 2),
                "reason": order.reason,
            }
        )
        self._save_state()
        return len(self._state["orders"])

    def _buy(self, order: OrderIntent) -> None:
        amount = order.quantity * order.price
        cash = float(self._state.get("cash", 0))
        if amount > cash:
            raise RuntimeError("paper broker cash not enough")
        positions = self._state.setdefault("positions", {})
        pos = positions.get(order.symbol, {"quantity": 0, "avg_price": 0.0, "last_price": order.price, "high_price": order.price})
        old_qty = int(pos["quantity"])
        old_cost = old_qty * float(pos["avg_price"])
        new_qty = old_qty + order.quantity
        pos["quantity"] = new_qty
        pos["avg_price"] = (old_cost + amount) / new_qty
        pos["last_price"] = order.price
        pos["high_price"] = max(float(pos.get("high_price", order.price) or order.price), order.price, pos["avg_price"])
        positions[order.symbol] = pos
        self._state["cash"] = cash - amount

    def _sell(self, order: OrderIntent) -> None:
        positions = self._state.setdefault("positions", {})
        pos = positions.get(order.symbol)
        if not pos or int(pos.get("quantity", 0)) < order.quantity:
            raise RuntimeError("paper broker position not enough")
        pos["quantity"] = int(pos["quantity"]) - order.quantity
        pos["last_price"] = order.price
        self._state["cash"] = float(self._state.get("cash", 0)) + order.quantity * order.price
        if int(pos["quantity"]) <= 0:
            positions.pop(order.symbol, None)
''', encoding='utf-8')

# 4) leader.py: make legacy leader name use real DragonHeadStrategy instead of momentum pseudo-leader.
leader = root / 'stockbot/strategies/leader.py'
leader.write_text('''"""Compatibility wrapper for the real dragon-head strategy.

The old LeaderStrategy was a momentum/trend model and could filter out limit-up
stocks, which contradicted 龙头战法. Keep the public class name for old config
compatibility, but delegate all decisions to DragonHeadStrategy.
"""
from __future__ import annotations

from stockbot.core.models import Signal
from stockbot.strategies.dragon_head import DragonHeadStrategy


class LeaderStrategy:
    def __init__(self, cfg: dict):
        self.impl = DragonHeadStrategy(cfg)

    def generate(self, bars_by_symbol: dict[str, object]) -> list[Signal]:
        return self.impl.generate(bars_by_symbol)

    def rank(self, bars_by_symbol: dict[str, object]) -> list[dict]:
        rows: list[dict] = []
        for symbol, df in bars_by_symbol.items():
            item = self.impl.score_one(symbol, df)
            if item:
                rows.append(item)
        rows.sort(key=lambda x: x["score"], reverse=True)
        return rows

    def score_one(self, symbol: str, df: object) -> dict | None:
        return self.impl.score_one(symbol, df)
''', encoding='utf-8')

# 5) dragon_head.py: project-root path resolution + runtime pool support for no-future backtest.
dh = root / 'stockbot/strategies/dragon_head.py'
text = dh.read_text(encoding='utf-8')
text = text.replace('from stockbot.core.models import Signal, Side\n', 'from stockbot.core.models import Signal, Side\n\nPROJECT_ROOT = Path(__file__).resolve().parents[2]\n\n\ndef _project_path(path_text: str | Path) -> Path:\n    path = Path(path_text)\n    return path if path.is_absolute() else PROJECT_ROOT / path\n')
text = text.replace('        self.market_context = self._load_json(cfg.get("market_context", {}).get("path", "stockbot/data/market_context.json"))\n        self.dragon_pool = self._load_pool(self.params.pool_path)\n        self.dragon_pool = self._with_sector_rank(self.dragon_pool)\n', '        self.market_context = self._load_json(cfg.get("market_context", {}).get("path", "stockbot/data/market_context.json"))\n        self.base_dragon_pool = self._load_pool(self.params.pool_path)\n        self.dragon_pool = self._with_sector_rank(self.base_dragon_pool)\n')
insert = '''\n    def set_runtime_pool(self, pool: pd.DataFrame) -> None:\n        """Inject a point-in-time pool, used by backtests to avoid future data."""\n        self.dragon_pool = self._with_sector_rank(pool)\n\n    def set_trade_date(self, trade_date: object) -> None:\n        """Use only the pool rows for trade_date when the pool file has a date column."""\n        if self.base_dragon_pool.empty or "date" not in self.base_dragon_pool.columns:\n            return\n        dates = pd.to_datetime(self.base_dragon_pool["date"], errors="coerce").dt.date\n        target = pd.to_datetime(trade_date).date()\n        self.dragon_pool = self._with_sector_rank(self.base_dragon_pool[dates == target].copy())\n'''
text = text.replace('    def generate(self, bars_by_symbol: dict[str, object]) -> list[Signal]:\n', insert + '\n    def generate(self, bars_by_symbol: dict[str, object]) -> list[Signal]:\n')
text = text.replace('        p = Path(path)\n        if not p.exists():', '        p = _project_path(path)\n        if not p.exists():')
text = text.replace('        p = Path(path)\n        if not p.exists():', '        p = _project_path(path)\n        if not p.exists():')
dh.write_text(text, encoding='utf-8')

# 6) report.py: strict DictReader only; no implicit CSV guessing branch.
report = root / 'stockbot/core/report.py'
text = report.read_text(encoding='utf-8')
text = text.replace('                if path == self.trade_log and not row.get("amount"):\n                    row["amount"] = self._calc_amount(row)\n', '')
start = text.find('    @staticmethod\n    def _calc_amount')
if start != -1:
    end = text.find('    def _read_account', start)
    text = text[:start] + text[end:]
text = text.replace('            f"- 止盈线：{float(self.cfg[\'risk\'][\'take_profit_pct\']):.0%}",\n', '            f"- 止盈线：{float(self.cfg[\'risk\'][\'take_profit_pct\']):.0%}",\n            f"- 移动止盈回撤线：{float(self.cfg[\'risk\'].get(\'trailing_stop_pct\', 0)):.0%}",\n')
report.write_text(text, encoding='utf-8')

# 7) build_market_context.py: do not refresh before 11:00 unless forced; fallback unknown instead of false panic.
bmc = root / 'scripts/build_market_context.py'
bmc.write_text('''"""Build market emotion cycle and sector heat context."""
from __future__ import annotations

import argparse
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


def _write_unknown_context(output: str, reason: str) -> Path:
    heat = SectorHeatAnalyzer({})
    emotion = EmotionSnapshot("unknown", 0.0, 0, 0, 0, 0, 0, 0, 0, 0.0)
    return heat.save_context(emotion, [], [], str(_project_path(output)))


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
        emotion = EmotionSnapshot("unknown", 0.0, 0, 0, 0, 0, 0, 0, 0, 0.0)
        path = heat.save_context(emotion, [], [], str(output_path))
        print(f"market_context={path}")
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
''', encoding='utf-8')

# 8) daily_run.py: market_context optional and early-refresh safe.
daily = root / 'scripts/daily_run.py'
text = daily.read_text(encoding='utf-8')
text = text.replace('        "ok": all(step["returncode"] == 0 for step in steps),', '        "ok": all(step["returncode"] == 0 or step.get("optional") for step in steps),')
text = text.replace('''    steps.append(
        run_step(
            "build_market_context",
            [str(PYTHON), "scripts/build_market_context.py", "--config", args.config, "--top", "20", "--timeout", "20"],
            min(args.timeout, 120),
        )
    )
''', '''    market_context_step = run_step(
        "build_market_context",
        [
            str(PYTHON),
            "scripts/build_market_context.py",
            "--config",
            args.config,
            "--top",
            "20",
            "--timeout",
            "20",
            "--allow-fallback",
            "--min-refresh-time",
            "11:00",
        ],
        min(args.timeout, 120),
    )
    market_context_step["optional"] = True
    steps.append(market_context_step)
''')
text = text.replace('    ok = all(step["returncode"] == 0 for step in steps)\n', '    ok = all(step["returncode"] == 0 or step.get("optional") for step in steps)\n')
daily.write_text(text, encoding='utf-8')

# 9) backtest.py: trailing stop + point-in-time dragon pool to avoid static future pool.
bt = root / 'stockbot/core/backtest.py'
text = bt.read_text(encoding='utf-8')
text = text.replace('        take_profit = float(risk["take_profit_pct"])\n', '        take_profit = float(risk["take_profit_pct"])\n        trailing_stop = float(risk.get("trailing_stop_pct", 0) or 0)\n')
text = text.replace('''            signals = self.strategy.generate(signal_bars)
''', '''            if isinstance(self.strategy, DragonHeadStrategy):
                if bool(self.cfg.get("backtest", {}).get("use_static_dragon_pool", False)):
                    self.strategy.set_trade_date(signal_date)
                else:
                    self.strategy.set_runtime_pool(self._point_in_time_dragon_pool(signal_bars))
            signals = self.strategy.generate(signal_bars)
''')
text = text.replace('''                pnl_pct = price / pos["avg_price"] - 1
                if pnl_pct <= -stop_loss or pnl_pct >= take_profit:
                    value = self._sell_value(pos["quantity"], price)
                    cash += value
                    closed_returns.append(pnl_pct)
                    trade_rows.append(self._trade_row(exec_date, symbol, Side.SELL, pos["quantity"], price, "stop_loss" if pnl_pct <= -stop_loss else "take_profit"))
                    positions.pop(symbol)
''', '''                high_price = max(float(pos.get("high_price", pos["avg_price"])), price)
                pos["high_price"] = high_price
                pnl_pct = price / pos["avg_price"] - 1
                trailing_hit = trailing_stop > 0 and high_price > pos["avg_price"] and price / high_price - 1 <= -trailing_stop
                if pnl_pct <= -stop_loss or pnl_pct >= take_profit or trailing_hit:
                    value = self._sell_value(pos["quantity"], price)
                    cash += value
                    closed_returns.append(pnl_pct)
                    reason = "stop_loss" if pnl_pct <= -stop_loss else "take_profit" if pnl_pct >= take_profit else "trailing_stop"
                    trade_rows.append(self._trade_row(exec_date, symbol, Side.SELL, pos["quantity"], price, reason))
                    positions.pop(symbol)
''')
text = text.replace('''                positions[symbol] = {"quantity": qty, "avg_price": price * (1 + self.slippage), "last_price": close_prices.get(symbol, price)}
''', '''                avg_price = price * (1 + self.slippage)
                positions[symbol] = {"quantity": qty, "avg_price": avg_price, "last_price": close_prices.get(symbol, price), "high_price": max(avg_price, close_prices.get(symbol, price))}
''')
text = text.replace('''                pos["last_price"] = close_prices.get(symbol, pos["last_price"])
                position_value += pos["quantity"] * pos["last_price"]
''', '''                pos["last_price"] = close_prices.get(symbol, pos["last_price"])
                pos["high_price"] = max(float(pos.get("high_price", pos["last_price"])), pos["last_price"])
                position_value += pos["quantity"] * pos["last_price"]
''')
insert = '''\n    @staticmethod\n    def _point_in_time_dragon_pool(signal_bars: dict[str, pd.DataFrame]) -> pd.DataFrame:\n        """Build a same-day涨停池 approximation from bars visible at signal_date.\n\n        This avoids the backtest reading today's static dragon_pool.csv for past\n        dates. It keeps only data available at the signal date; intraday fields\n        like seal amount are set conservatively when unavailable.\n        """\n        rows: list[dict[str, Any]] = []\n        for symbol, df in signal_bars.items():\n            if df is None or df.empty:\n                continue\n            latest = df.iloc[-1]\n            pct = float(latest.get("pct_change", 0) or 0)\n            close = float(latest.get("close", 0) or 0)\n            high = float(latest.get("high", close) or close)\n            amount = float(latest.get("amount", 0) or 0)\n            if pct < 9.5 or close <= 0:\n                continue\n            streak = 0\n            for x in reversed(pd.to_numeric(df.get("pct_change", pd.Series(dtype=float)).tail(10), errors="coerce").fillna(0).tolist()):\n                if float(x) >= 9.5:\n                    streak += 1\n                else:\n                    break\n            rows.append({\n                "symbol": symbol,\n                "name": str(latest.get("name", "")),\n                "sector": str(latest.get("sector", "未分组")) or "未分组",\n                "limit_up_count": max(streak, 1),\n                "score": pct,\n                "price": close,\n                "pct_change": pct,\n                "amount": amount,\n                "seal_amount": float(latest.get("seal_amount", 0) or 0),\n                "turnover": float(latest.get("turnover", 0) or 0),\n                "first_limit_time": float(latest.get("first_limit_time", 999999) or 999999),\n                "last_limit_time": float(latest.get("last_limit_time", 999999) or 999999),\n                "open_times": int(float(latest.get("open_times", 0) or 0)),\n                "high": high,\n            })\n        return pd.DataFrame(rows)\n'''
text = text.replace('    @staticmethod\n    def _cannot_buy', insert + '\n    @staticmethod\n    def _cannot_buy')
text = text.replace('''            "daily_sharpe_approx": round(float(daily_ret.mean() / daily_ret.std() * (252 ** 0.5)), 4) if len(daily_ret) > 2 and daily_ret.std() > 0 else 0,
''', '''            "annualized_sharpe": round(float(daily_ret.mean() / daily_ret.std() * (252 ** 0.5)), 4) if len(daily_ret) > 2 and daily_ret.std() > 0 else 0,
            "sharpe_note": "按每日权益收益年化，仅用于同策略横向比较；胜率使用已平仓回合统计。",
''')
bt.write_text(text, encoding='utf-8')

# 10) config: order limits to match 100k paper account + trailing active.
cfg = root / 'config.yaml'
text = cfg.read_text(encoding='utf-8')
text = text.replace('  max_daily_buy_amount: 10000\n  max_single_order_amount: 3000\n', '  max_daily_buy_amount: 30000\n  max_single_order_amount: 10000\n')
cfg.write_text(text, encoding='utf-8')

print('fixed')
