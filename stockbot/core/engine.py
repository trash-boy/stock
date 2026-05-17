"""Trading engine."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from stockbot.core.models import AccountSnapshot, OrderIntent, Side, Signal
from stockbot.core.risk import RiskManager, RiskReject

try:
    from stockbot.strategies.dragon_head_exit import DragonHeadExitStrategy
except Exception:
    DragonHeadExitStrategy = None  # type: ignore


class TradingEngine:
    def __init__(self, cfg: dict, broker):
        self.cfg = cfg
        self.broker = broker
        self.risk = RiskManager(cfg)
        self.today_buy_amount = self._load_today_buy_amount()
        # 龙头式卖出器 — 仅当配置开启 dragon_head_exit.enabled
        self._exit_strategy = None
        if DragonHeadExitStrategy and (cfg.get("dragon_head_exit", {}) or {}).get("enabled"):
            try:
                self._exit_strategy = DragonHeadExitStrategy(cfg)
            except Exception as exc:
                import sys
                print(f"warn: DragonHeadExitStrategy init failed: {exc}", file=sys.stderr)

    def signal_to_order(self, account: AccountSnapshot, signal: Signal) -> OrderIntent | None:
        if signal.price is None or signal.price <= 0:
            return None
        if signal.side == Side.BUY and signal.symbol in account.positions:
            return None
        if signal.side == Side.SELL:
            pos = account.positions.get(signal.symbol)
            if not pos:
                return None
            return OrderIntent(signal.symbol, Side.SELL, pos.quantity, signal.price, signal.reason)
        target_amount = min(
            account.total_asset * float(self.cfg["risk"]["max_single_position_pct"]) * signal.weight,
            float(self.cfg["risk"]["max_single_order_amount"]),
        )
        lot = int(self.cfg["execution"].get("lot_size", 100))
        qty = int(target_amount // (signal.price * lot)) * lot
        if qty <= 0:
            return None
        return OrderIntent(signal.symbol, Side.BUY, qty, signal.price, signal.reason)

    def execute(self, signals: list[Signal], bars_by_symbol: dict | None = None) -> list[OrderIntent]:
        for signal in signals:
            if signal.price is not None and hasattr(self.broker, "mark_price"):
                self.broker.mark_price(signal.symbol, float(signal.price))
        account = self.broker.snapshot()
        # 优先级:龙头式卖出 > 通用止损 > 新买入
        candidate_orders: list[OrderIntent] = []
        if self._exit_strategy is not None:
            try:
                dragon_sells = self._exit_strategy.generate_sells(account, bars_by_symbol or {})
                candidate_orders.extend(dragon_sells)
                # 已经被龙头式 sell 覆盖的票,不再走通用 stop_loss
                covered = {o.symbol for o in dragon_sells}
                for o in self.risk.stop_orders(account):
                    if o.symbol not in covered:
                        candidate_orders.append(o)
            except Exception as exc:
                import sys
                print(f"warn: dragon_head_exit failed: {exc}", file=sys.stderr)
                candidate_orders.extend(self.risk.stop_orders(account))
        else:
            candidate_orders.extend(self.risk.stop_orders(account))
        for signal in signals:
            if len(candidate_orders) >= int(self.cfg["risk"]["max_positions"]):
                break
            order = self.signal_to_order(account, signal)
            if order:
                candidate_orders.append(order)

        accepted: list[OrderIntent] = []
        for order in candidate_orders:
            try:
                self.risk.validate_order(account, order, self.today_buy_amount)
                self.broker.place_order(order)
            except RiskReject as exc:
                self._log_reject(order, str(exc))
                continue
            if order.side == Side.BUY:
                self.today_buy_amount += order.quantity * order.price
            accepted.append(order)
            self._log_order(order)
        return accepted

    def _load_today_buy_amount(self) -> float:
        path = Path(self.cfg["logging"]["trade_log"])
        if not path.exists():
            return 0.0
        today = datetime.now().date().isoformat()
        total = 0.0
        try:
            with path.open("r", newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if not row.get("time", "").startswith(today):
                        continue
                    if row.get("side") == Side.BUY.value:
                        total += int(float(row.get("quantity", 0))) * float(row.get("price", 0))
        except Exception:
            return 0.0
        return total

    def _log_order(self, order: OrderIntent) -> None:
        path = Path(self.cfg["logging"]["trade_log"])
        header = ["time", "symbol", "side", "quantity", "price", "amount", "reason"]
        self._ensure_csv_schema(path, header)
        exists = path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow(header)
            writer.writerow([
                datetime.now().isoformat(timespec="seconds"),
                order.symbol,
                order.side.value,
                order.quantity,
                order.price,
                round(order.quantity * order.price, 2),
                order.reason,
            ])

    def _log_reject(self, order: OrderIntent, reject_reason: str) -> None:
        path = Path(self.cfg.get("logging", {}).get("reject_log", "stockbot/logs/rejections.csv"))
        header = ["time", "symbol", "side", "quantity", "price", "amount", "signal_reason", "reject_reason"]
        self._ensure_csv_schema(path, header)
        exists = path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow(header)
            writer.writerow([
                datetime.now().isoformat(timespec="seconds"),
                order.symbol,
                order.side.value,
                order.quantity,
                order.price,
                round(order.quantity * order.price, 2),
                order.reason,
                reject_reason,
            ])
    @staticmethod
    def _ensure_csv_schema(path: Path, header: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.stat().st_size == 0:
            return
        try:
            with path.open("r", newline="", encoding="utf-8") as f:
                first = next(csv.reader(f), [])
        except Exception:
            first = []
        if first != header:
            backup = path.with_name(f"{path.stem}.legacy_{datetime.now().strftime('%Y%m%d_%H%M%S')}{path.suffix}")
            path.replace(backup)

