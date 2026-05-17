"""Core domain models."""
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
