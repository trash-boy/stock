"""Compatibility wrapper for the real dragon-head strategy.

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
