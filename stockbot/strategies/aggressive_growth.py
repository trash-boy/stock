"""Aggressive A-share growth strategy skeleton.

The strategy is intentionally rule-based: emotion is not an input.
"""
from __future__ import annotations

from stockbot.core.models import Signal, Side


class AggressiveGrowthStrategy:
    """偏积极成长风格：强趋势 + 放量 + 避开传统行业。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.universe = cfg["universe"]

    def generate(self, bars_by_symbol: dict[str, object]) -> list[Signal]:
        """Generate signals from precomputed bar data.

        Expected data per symbol can be a pandas DataFrame with columns:
        close, volume, ma20, ma60, volume_ma20, industry.
        """
        signals: list[Signal] = []
        for symbol, bars in bars_by_symbol.items():
            try:
                latest = bars.iloc[-1]
                name = str(latest.get("name", ""))
                industry = str(latest.get("industry", ""))
                if self._excluded(name, industry):
                    continue
                close = float(latest["close"])
                ma20 = float(latest["ma20"])
                ma60 = float(latest["ma60"])
                volume = float(latest["volume"])
                volume_ma20 = float(latest["volume_ma20"])
                if close > ma20 > ma60 and volume > 1.5 * volume_ma20:
                    weight = 1.0 if self._preferred(industry) else 0.6
                    signals.append(Signal(symbol, Side.BUY, weight, f"trend_breakout:{industry}", close))
            except Exception:
                continue
        return sorted(signals, key=lambda x: x.weight, reverse=True)

    def _excluded(self, name: str, industry: str) -> bool:
        if any(name.startswith(prefix) for prefix in self.universe["exclude_prefixes"]):
            return True
        return industry in set(self.universe["avoid_industries"])

    def _preferred(self, industry: str) -> bool:
        return any(key in industry for key in self.universe["preferred_industries"])
