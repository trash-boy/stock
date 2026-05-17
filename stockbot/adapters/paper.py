"""Paper broker for local simulation.

It persists a simple cash/position ledger to JSON and executes orders at the
price passed by the strategy. This is only for validating strategy + risk logic.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from stockbot.core.models import AccountSnapshot, OrderIntent, Position, Side

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

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
        tmp = self.state_path.with_suffix(self.state_path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

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
        with self._locked_state():
            self._state = self._load_state()
            self._migrate_state()
            # Hard dedup: 同票已有持仓时 BUY 直接拒绝(防止 strategy 重复信号 / 手工注单加仓)
            if order.side == Side.BUY:
                existing = self._state.get("positions", {}).get(order.symbol, {})
                if int(existing.get("quantity", 0) or 0) > 0:
                    raise RuntimeError(f"paper broker reject duplicate BUY: {order.symbol} already held")
                self._buy(order)
            else:
                self._sell(order)
            self._state.setdefault("orders", []).append(
                {
                    "executed_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
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


    @contextmanager
    def _locked_state(self):
        lock_path = self.state_path.with_suffix(self.state_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

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
