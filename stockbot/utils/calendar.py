"""A股交易日历工具。

优先调用 akshare 的 tool_trade_date_hist_sina 拉取并缓存到本地 csv,
之后离线判断 is_trade_day。每年自动刷新一次。
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_PATH = PROJECT_ROOT / "stockbot" / "data" / "trade_calendar.csv"
_CACHE: set[str] | None = None


def _refresh_cache() -> set[str]:
    """Try akshare; on any failure fall back to weekday heuristic."""
    try:
        import akshare as ak  # type: ignore

        df = ak.tool_trade_date_hist_sina()
        # 列名为 trade_date,值类型为 datetime.date 或 str
        col = "trade_date" if "trade_date" in df.columns else df.columns[0]
        days = {str(v)[:10] for v in df[col].tolist()}
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CACHE_PATH.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["trade_date"])
            for d in sorted(days):
                w.writerow([d])
        return days
    except Exception:
        return set()


def _load_cache() -> set[str]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if CACHE_PATH.exists():
        days: set[str] = set()
        with CACHE_PATH.open("r", encoding="utf-8") as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                if row:
                    days.add(row[0].strip())
        # 缓存若过老(超过 365 天没更新或没覆盖未来 30 天),刷新一次
        try:
            mtime = datetime.fromtimestamp(CACHE_PATH.stat().st_mtime).date()
            stale = (date.today() - mtime).days > 30
        except Exception:
            stale = True
        future_ok = any(d for d in days if d > date.today().isoformat())
        if stale or not future_ok:
            refreshed = _refresh_cache()
            if refreshed:
                days = refreshed
        _CACHE = days
        return _CACHE
    refreshed = _refresh_cache()
    _CACHE = refreshed
    return _CACHE


def is_trade_day(d: date | None = None) -> bool:
    """Return True if d is an A-share trading day.

    Falls back to weekday-only check if no cache and akshare unreachable.
    """
    d = d or date.today()
    days = _load_cache()
    if days:
        return d.isoformat() in days
    # 兜底:周一到周五视为交易日
    return d.weekday() < 5


__all__ = ["is_trade_day"]
