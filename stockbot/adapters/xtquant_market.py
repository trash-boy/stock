"""xtquant market data adapter.

只负责行情读取，不负责交易下单。QMT/miniQMT 客户端需要已安装并可被当前 Python 环境 import xtquant。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class HistoryRequest:
    symbols: list[str]
    period: str = "1d"
    count: int = 120
    dividend_type: str = "front"


class XtQuantMarketData:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        """Normalize A-share code to xtquant style: 000001.SZ / 600000.SH."""
        symbol = symbol.strip().upper()
        if "." in symbol:
            return symbol
        if symbol.startswith(("6", "9")):
            return f"{symbol}.SH"
        return f"{symbol}.SZ"

    def get_history(self, request: HistoryRequest) -> dict[str, object]:
        try:
            from xtquant import xtdata
        except ImportError as exc:
            raise RuntimeError("未安装或未配置 xtquant：请确认 QMT/miniQMT 的 Python 环境可 import xtquant") from exc

        stock_list = [self.normalize_symbol(s) for s in request.symbols]
        data = xtdata.get_market_data_ex(
            field_list=[],
            stock_list=stock_list,
            period=request.period,
            start_time="",
            end_time="",
            count=request.count,
            dividend_type=request.dividend_type,
            fill_data=True,
        )
        return {symbol: self._with_indicators(frame) for symbol, frame in data.items() if frame is not None and len(frame) > 0}

    def get_latest_quotes(self, symbols: Iterable[str]) -> dict[str, dict]:
        try:
            from xtquant import xtdata
        except ImportError as exc:
            raise RuntimeError("未安装或未配置 xtquant：请确认 QMT/miniQMT 的 Python 环境可 import xtquant") from exc
        stock_list = [self.normalize_symbol(s) for s in symbols]
        return xtdata.get_full_tick(stock_list) or {}

    @staticmethod
    def _with_indicators(frame: object) -> object:
        """Add strategy columns expected by AggressiveGrowthStrategy."""
        df = frame.copy()
        if "close" not in df.columns or "volume" not in df.columns:
            return df
        df["ma20"] = df["close"].rolling(20).mean()
        df["ma60"] = df["close"].rolling(60).mean()
        df["volume_ma20"] = df["volume"].rolling(20).mean()
        if "name" not in df.columns:
            df["name"] = ""
        if "industry" not in df.columns:
            df["industry"] = ""
        return df.dropna(subset=["ma20", "ma60", "volume_ma20"])
