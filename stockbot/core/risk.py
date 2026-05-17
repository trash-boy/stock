"""Risk manager: every live order must pass this gate."""
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
        env = runtime.get("env")
        # live 模式必须 enabled
        if env == "live" and runtime.get("enabled") is not True:
            raise RiskReject("实盘模式未启用：请在 config.yaml 设置 runtime.enabled=true")
        # alert 模式支持紧急熔断:把 runtime.enabled=false 当作"暂停推送"开关
        # 仅当显式 False (不是 None/缺省) 才熔断;默认放行
        if env == "alert" and runtime.get("enabled") is False:
            raise RiskReject("alert 模式已被紧急熔断:runtime.enabled=false")

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
