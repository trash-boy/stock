"""QMT / miniQMT adapter based on xtquant.

This file keeps all broker-specific calls isolated. Import errors are delayed so
paper mode can run without xtquant installed.
"""
from __future__ import annotations

from stockbot.core.models import AccountSnapshot, OrderIntent, Position, Side


class QmtAdapter:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.qmt = cfg["qmt"]
        self.trader = None
        self.account = None

    def connect(self) -> None:
        try:
            from xtquant.xttrader import XtQuantTrader
            from xtquant.xttype import StockAccount
        except ImportError as exc:
            raise RuntimeError("未安装或未配置 xtquant，请先安装 QMT/miniQMT 并确认 Python 可 import xtquant") from exc
        self.trader = XtQuantTrader(self.qmt["path"], int(self.qmt["session_id"]))
        self.account = StockAccount(self.qmt["account_id"], self.qmt.get("account_type", "STOCK"))
        self.trader.start()
        connect_result = self.trader.connect()
        if connect_result != 0:
            raise RuntimeError(f"QMT connect failed: {connect_result}")
        subscribe_result = self.trader.subscribe(self.account)
        if subscribe_result != 0:
            raise RuntimeError(f"QMT subscribe failed: {subscribe_result}")

    def snapshot(self) -> AccountSnapshot:
        if self.trader is None or self.account is None:
            raise RuntimeError("QMT 未连接")
        asset = self.trader.query_stock_asset(self.account)
        positions_raw = self.trader.query_stock_positions(self.account) or []
        positions: dict[str, Position] = {}
        for p in positions_raw:
            quantity = int(getattr(p, "volume", 0) or 0)
            if quantity <= 0:
                continue
            symbol = getattr(p, "stock_code")
            avg_price = float(getattr(p, "open_price", 0) or 0)
            last_price = float(getattr(p, "market_value", 0) or 0) / quantity if quantity else avg_price
            positions[symbol] = Position(symbol, quantity, avg_price, last_price)
        return AccountSnapshot(
            cash=float(getattr(asset, "cash", 0) or 0),
            total_asset=float(getattr(asset, "total_asset", 0) or 0),
            positions=positions,
        )

    def place_order(self, order: OrderIntent) -> int:
        if self.trader is None or self.account is None:
            raise RuntimeError("QMT 未连接")
        try:
            from xtquant import xtconstant
        except ImportError as exc:
            raise RuntimeError("未安装或未配置 xtquant") from exc
        op_type = xtconstant.STOCK_BUY if order.side == Side.BUY else xtconstant.STOCK_SELL
        price_type = xtconstant.FIX_PRICE
        return self.trader.order_stock(
            self.account,
            order.symbol,
            op_type,
            order.quantity,
            price_type,
            order.price,
            "stockbot",
            order.reason,
        )
