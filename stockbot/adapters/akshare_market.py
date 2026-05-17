"""AkShare market data adapter for macOS-friendly A-share data.

AkShare is used only for market data and screening. It is not a broker and
cannot place live orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import time

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_ROOT = Path.home() / ".cache" / "stockbot"


@dataclass(frozen=True)
class AkshareHistoryRequest:
    symbols: list[str]
    period: str = "daily"
    count: int = 120
    adjust: str = "qfq"


class AkshareMarketData:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        cache_cfg = cfg.get("market_data", {}).get("cache", {})
        self.cache_enabled = bool(cache_cfg.get("enabled", True))
        self.cache_root = Path(cache_cfg.get("root", CACHE_ROOT / "history")).expanduser()
        self.retries = int(cfg.get("market_data", {}).get("retries", 5))
        self.retry_sleep = float(cfg.get("market_data", {}).get("retry_sleep", 1.2))

    @staticmethod
    def strip_exchange(symbol: str) -> str:
        symbol = str(symbol).strip().upper()
        if symbol.startswith(("SH", "SZ", "BJ")):
            symbol = symbol[2:]
        return symbol.split(".", 1)[0].zfill(6)

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        code = AkshareMarketData.strip_exchange(symbol)
        if code.startswith(("6", "9")):
            return f"{code}.SH"
        return f"{code}.SZ"

    def get_history(self, request: AkshareHistoryRequest) -> dict[str, object]:
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError("未安装 akshare，请执行：.venv/bin/pip install akshare") from exc

        name_map = self._load_name_map(request.symbols)
        result: dict[str, object] = {}
        for raw_symbol in request.symbols:
            code = self.strip_exchange(raw_symbol)
            symbol = self.normalize_symbol(raw_symbol)
            cached = self._read_history_cache(symbol)
            min_rows = max(80, request.count if request.count > 0 else 80)
            recent_enough = self._cache_recent_enough(cached) if cached is not None else False
            need_fetch = cached is None or len(cached) < min_rows or not recent_enough
            raw = cached
            last_error: Exception | None = None
            if need_fetch:
                for attempt in range(self.retries):
                    try:
                        raw = ak.stock_zh_a_hist(symbol=code, period=request.period, adjust=request.adjust)
                        self._write_history_cache(symbol, raw)
                        break
                    except Exception as exc:
                        last_error = exc
                        time.sleep(self.retry_sleep * (attempt + 1))
                if raw is None:
                    # 尝试 baostock 回退
                    raw = self._fetch_baostock(code, raw_symbol)
                    if raw is None or len(raw) == 0:
                        print(f"warn: skip {raw_symbol}, akshare error: {last_error}")
                        continue
                    self._write_history_cache(symbol, raw)
                    print(f"info: {raw_symbol} fallback to baostock OK ({len(raw)} bars)")
            if raw is None or len(raw) == 0:
                continue
            df = raw.tail(request.count) if request.count > 0 else raw
            result[symbol] = self._normalize_frame(df, symbol, name_map.get(symbol, ""))
        return result

    def _load_name_map(self, symbols: list[str]) -> dict[str, str]:
        try:
            spot = self.get_spot()
            wanted = {self.strip_exchange(s) for s in symbols}
            codes = spot["代码"].astype(str).str.lower().str.replace(r"^(sh|sz|bj)", "", regex=True).str.zfill(6)
            rows = spot[codes.isin(wanted)].copy()
            rows["_symbol"] = codes[codes.isin(wanted)].map(self.normalize_symbol)
            return dict(zip(rows["_symbol"].astype(str), rows["名称"].astype(str)))
        except Exception:
            return {}

    @staticmethod
    def _cache_recent_enough(df: object) -> bool:
        if df is None or len(df) == 0 or "日期" not in df.columns:
            return False
        try:
            last = pd.to_datetime(df["日期"].iloc[-1]).date()
            today = pd.Timestamp.today().date()
            return (today - last).days <= 7
        except Exception:
            return False

    def _cache_path(self, symbol: str) -> Path:
        return self.cache_root / f"{symbol}.csv"

    def _read_history_cache(self, symbol: str):
        if not self.cache_enabled:
            return None
        path = self._cache_path(symbol)
        if not path.exists():
            return None
        try:
            return pd.read_csv(path)
        except Exception:
            return None

    def _write_history_cache(self, symbol: str, df: object) -> None:
        if not self.cache_enabled:
            return
        path = self._cache_path(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        df.to_csv(tmp, index=False, encoding="utf-8-sig")
        tmp.replace(path)

    def get_spot(self) -> object:
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError("未安装 akshare，请执行：.venv/bin/pip install akshare") from exc
        errors: list[Exception] = []
        for func_name in ("stock_zh_a_spot_em", "stock_zh_a_spot"):
            for attempt in range(max(2, self.retries)):
                try:
                    raw = getattr(ak, func_name)()
                    return self._normalize_spot_columns(raw)
                except Exception as exc:
                    errors.append(exc)
                    time.sleep(self.retry_sleep * (attempt + 1))
        raise RuntimeError(f"AkShare spot 接口均失败: {errors[-1] if errors else 'unknown'}")

    @staticmethod
    def _normalize_spot_columns(df: object) -> object:
        data = df.copy()
        rename = {
            "symbol": "代码", "code": "代码", "name": "名称", "trade": "最新价", "price": "最新价",
            "changepercent": "涨跌幅", "volume": "成交量", "amount": "成交额", "turnoverratio": "换手率",
            "mktcap": "总市值", "nmc": "流通市值",
        }
        for src, dst in rename.items():
            if src in data.columns and dst not in data.columns:
                data = data.rename(columns={src: dst})
        required = ["代码", "名称", "最新价", "涨跌幅", "成交量", "成交额", "换手率", "流通市值"]
        for col in required:
            if col not in data.columns:
                data[col] = 0 if col not in {"代码", "名称"} else ""
        return data

    def get_concept_boards(self) -> object:
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError("未安装 akshare，请执行：.venv/bin/pip install akshare") from exc
        return ak.stock_board_concept_name_em()

    def get_industry_boards(self) -> object:
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError("未安装 akshare，请执行：.venv/bin/pip install akshare") from exc
        return ak.stock_board_industry_name_em()

    def get_latest_quotes(self, symbols: Iterable[str]) -> dict[str, dict]:
        spot = self.get_spot()
        wanted = {self.strip_exchange(s) for s in symbols}
        rows = spot[spot["代码"].astype(str).str.lower().str.replace(r"^(sh|sz|bj)", "", regex=True).str.zfill(6).isin(wanted)]
        quotes: dict[str, dict] = {}
        for _, row in rows.iterrows():
            symbol = self.normalize_symbol(str(row["代码"]))
            quotes[symbol] = row.to_dict()
        return quotes

    def _fetch_baostock(self, code: str, raw_symbol: str):
        """AkShare 失败回退:用 baostock 取日线。返回 AkShare 同款列名 DataFrame 或 None。"""
        try:
            import baostock as bs
        except Exception:
            return None
        # baostock 代码格式: sh.600999 / sz.300999
        if raw_symbol.endswith(".SH"):
            bs_code = f"sh.{code}"
        elif raw_symbol.endswith(".SZ"):
            bs_code = f"sz.{code}"
        elif raw_symbol.endswith(".BJ"):
            bs_code = f"bj.{code}"
        else:
            return None
        try:
            from datetime import date, timedelta
            end = date.today()
            start = end - timedelta(days=400)
            lg = bs.login()
            if lg.error_code != "0":
                return None
            try:
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,open,high,low,close,volume,amount,pctChg,turn",
                    start_date=start.strftime("%Y-%m-%d"),
                    end_date=end.strftime("%Y-%m-%d"),
                    frequency="d", adjustflag="2",
                )
                rows = []
                while rs.error_code == "0" and rs.next():
                    rows.append(rs.get_row_data())
            finally:
                bs.logout()
            if not rows:
                return None
            cols = ["date", "open", "high", "low", "close", "volume", "amount", "pct_change", "turnover"]
            df = pd.DataFrame(rows, columns=cols)
            # 转回 AkShare 中文列名以走 _normalize_frame
            df = df.rename(columns={
                "date": "日期", "open": "开盘", "high": "最高", "low": "最低",
                "close": "收盘", "volume": "成交量", "amount": "成交额",
                "pct_change": "涨跌幅", "turnover": "换手率",
            })
            return df
        except Exception:
            return None

    @staticmethod
    def _normalize_frame(df: object, symbol: str, name: str = "") -> object:
        data = df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount", "振幅": "amplitude", "涨跌幅": "pct_change", "涨跌额": "change", "换手率": "turnover"}).copy()
        data["symbol"] = symbol
        data["name"] = name or data.get("name", "")
        data["sector"] = data.get("sector", "")
        data["industry"] = data.get("industry", "")
        for col in ["open", "close", "high", "low", "volume", "amount", "pct_change"]:
            if col in data.columns:
                data[col] = pd.to_numeric(data[col], errors="coerce").fillna(0.0)
            else:
                data[col] = 0.0
        data["ma20"] = data["close"].rolling(20).mean()
        data["ma60"] = data["close"].rolling(60).mean()
        data["volume_ma20"] = data["volume"].rolling(20).mean()
        return data.dropna(subset=["ma20", "ma60", "volume_ma20"])
