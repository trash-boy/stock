"""龙头战法专用卖出逻辑 — 替代通用止损止盈,匹配龙头节奏。

四种卖点(按优先级从高到低):
1. emergency_exit       情绪转 cooldown/panic → 全仓 T+1 清仓
2. break_board_exit     持仓票当日炸板且未回封 → 收盘前一刻全卖
3. theme_dead_exit      持仓票所在板块当日无新涨停 + 龙头连板断板 → 次日开盘卖
4. momentum_failure     盘中放量滞涨(成交>=昨日 1.5x 但涨幅<3%) → 减半

调用约定:
    exit_strategy = DragonHeadExitStrategy(cfg)
    sell_signals = exit_strategy.generate_sells(account_snapshot, bars_by_symbol)
    # 由 engine 把这些 OrderIntent 加进 candidate_orders 之前

每个返回是 OrderIntent(SELL),数量 = 持仓 quantity * 减仓比例
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from stockbot.core.models import AccountSnapshot, OrderIntent, Side

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _project_path(path_text: str | Path) -> Path:
    p = Path(path_text)
    return p if p.is_absolute() else PROJECT_ROOT / p


@dataclass(frozen=True)
class ExitConfig:
    enabled: bool = True
    cooldown_full_clear: bool = True
    emergency_exit_phases: tuple[str, ...] = ("cooldown", "panic")
    break_board_exit: bool = True
    break_board_min_open_times: int = 1
    momentum_failure_exit: bool = True
    momentum_volume_ratio: float = 1.5
    momentum_max_pct: float = 3.0
    momentum_reduce_ratio: float = 0.5
    theme_dead_exit: bool = True


class DragonHeadExitStrategy:
    """生成龙头式卖出 OrderIntent。

    被 TradingEngine 在常规 risk.stop_orders() 之前调用,优先级最高。
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        raw = cfg.get("dragon_head_exit", {}) or {}
        self.params = ExitConfig(
            enabled=bool(raw.get("enabled", True)),
            cooldown_full_clear=bool(raw.get("cooldown_full_clear", True)),
            emergency_exit_phases=tuple(raw.get("emergency_exit_phases", ("cooldown", "panic"))),
            break_board_exit=bool(raw.get("break_board_exit", True)),
            break_board_min_open_times=int(raw.get("break_board_min_open_times", 1)),
            momentum_failure_exit=bool(raw.get("momentum_failure_exit", True)),
            momentum_volume_ratio=float(raw.get("momentum_volume_ratio", 1.5)),
            momentum_max_pct=float(raw.get("momentum_max_pct", 3.0)),
            momentum_reduce_ratio=float(raw.get("momentum_reduce_ratio", 0.5)),
            theme_dead_exit=bool(raw.get("theme_dead_exit", True)),
        )
        self.market_context = self._load_json(
            cfg.get("market_context", {}).get("path", "stockbot/data/market_context.json")
        )
        self.dragon_pool = self._load_pool(
            cfg.get("dragon_head_strategy", {}).get("pool_path", "stockbot/data/dragon_pool.csv")
        )
        # 持仓所在板块今日是否还有新涨停 — 由 dragon_pool 反推
        self._sectors_with_new_limit_up: set[str] = self._sectors_with_today_lu()

    # ------------------------------------------------------------------ #
    # public                                                             #
    # ------------------------------------------------------------------ #
    def generate_sells(
        self,
        account: AccountSnapshot,
        bars_by_symbol: dict[str, object] | None = None,
    ) -> list[OrderIntent]:
        if not self.params.enabled or not account.positions:
            return []

        bars = bars_by_symbol or {}
        phase = str(self.market_context.get("emotion", {}).get("phase", "unknown"))

        # 1. 紧急退场:cooldown / panic 全仓清
        if self.params.cooldown_full_clear and phase in self.params.emergency_exit_phases:
            return [
                OrderIntent(
                    pos.symbol, Side.SELL, pos.quantity, pos.last_price,
                    f"dragon_exit:emergency_exit/phase={phase}",
                )
                for pos in account.positions.values()
                if pos.quantity > 0 and pos.last_price > 0
            ]

        orders: list[OrderIntent] = []
        used_symbols: set[str] = set()

        for pos in account.positions.values():
            if pos.quantity <= 0 or pos.last_price <= 0 or pos.symbol in used_symbols:
                continue
            df = bars.get(pos.symbol)
            pool_row = self._pool_row(pos.symbol)

            # 2. 炸板退出
            if self.params.break_board_exit:
                if self._is_break_board(pool_row):
                    orders.append(OrderIntent(
                        pos.symbol, Side.SELL, pos.quantity, pos.last_price,
                        f"dragon_exit:break_board/open_times={pool_row.get('open_times', '?')}",
                    ))
                    used_symbols.add(pos.symbol)
                    continue

            # 3. 题材哑火
            if self.params.theme_dead_exit:
                dead_reason = self._theme_dead_reason(pool_row)
                if dead_reason:
                    orders.append(OrderIntent(
                        pos.symbol, Side.SELL, pos.quantity, pos.last_price,
                        f"dragon_exit:theme_dead/{dead_reason}",
                    ))
                    used_symbols.add(pos.symbol)
                    continue

            # 4. 动量失败 — 减半
            if self.params.momentum_failure_exit:
                if self._is_momentum_failure(df, pool_row):
                    qty = self._reduce_qty(pos.quantity, self.params.momentum_reduce_ratio)
                    if qty > 0:
                        orders.append(OrderIntent(
                            pos.symbol, Side.SELL, qty, pos.last_price,
                            "dragon_exit:momentum_failure",
                        ))
                        used_symbols.add(pos.symbol)
                        continue

        return orders

    # ------------------------------------------------------------------ #
    # rules                                                              #
    # ------------------------------------------------------------------ #
    def _is_break_board(self, pool_row: dict | None) -> bool:
        """炸板:open_times >= 1 且当前未达涨停(close < limit price)。"""
        if not pool_row:
            return False
        open_times = int(float(pool_row.get("open_times", 0) or 0))
        if open_times < self.params.break_board_min_open_times:
            return False
        # 必须是"昨日封板今日炸"或"今日封过又炸",可用 pct_change 反推
        pct = float(pool_row.get("pct_change", 0) or 0)
        # 临近涨停 0.5% 内视作仍在板上,不算炸板
        return pct < self._limit_pct(str(pool_row.get("symbol", ""))) - 0.5

    def _theme_dead_reason(self, pool_row: dict | None) -> str:
        """题材哑火:板块当日无新涨停 且 自身 limit_up_count 较昨日下降(断板)。"""
        if not pool_row:
            return ""
        sector = str(pool_row.get("sector", "")).strip()
        if not sector or sector in {"未分组", "未知"}:
            return ""
        if sector in self._sectors_with_new_limit_up:
            return ""
        # 板块今日无新涨停 且 自己今日并未涨停
        pct = float(pool_row.get("pct_change", 0) or 0)
        if pct >= self._limit_pct(str(pool_row.get("symbol", ""))) - 0.5:
            # 自己仍在板上,板块虽冷但本票还撑着 — 不退
            return ""
        return f"sector={sector}/no_new_lu"

    def _is_momentum_failure(self, df: object, pool_row: dict | None) -> bool:
        """放量滞涨:今日成交额 / 前 5 日均量 >= ratio,但 pct_change < momentum_max_pct。"""
        if df is None or len(df) < 6:
            return False
        try:
            today = df.iloc[-1]
            prev = df.iloc[-6:-1]
            today_amount = float(today.get("amount", 0) or 0)
            prev_avg = float(prev["amount"].mean()) if "amount" in prev.columns else 0.0
            if prev_avg <= 0:
                return False
            ratio = today_amount / prev_avg
            pct = float(today.get("pct_change", 0) or 0)
            if pool_row:
                pct = float(pool_row.get("pct_change", pct) or pct)
            return ratio >= self.params.momentum_volume_ratio and pct < self.params.momentum_max_pct
        except Exception:
            return False

    @staticmethod
    def _limit_pct(symbol: str) -> float:
        code = str(symbol).split(".", 1)[0]
        if code.startswith(("8", "4")):
            return 29.5
        if code.startswith(("300", "301", "688")):
            return 19.5
        return 9.5

    @staticmethod
    def _reduce_qty(qty: int, ratio: float) -> int:
        """A股 100 股整数倍卖出,向下取整。"""
        if qty <= 0:
            return 0
        target = int(qty * ratio)
        return (target // 100) * 100

    # ------------------------------------------------------------------ #
    # data loaders                                                       #
    # ------------------------------------------------------------------ #
    def _pool_row(self, symbol: str) -> dict | None:
        if self.dragon_pool.empty:
            return None
        rows = self.dragon_pool[self.dragon_pool["symbol"].astype(str) == symbol]
        if rows.empty:
            return None
        return rows.iloc[0].to_dict()

    def _sectors_with_today_lu(self) -> set[str]:
        if self.dragon_pool.empty:
            return set()
        try:
            df = self.dragon_pool
            if "limit_up_count" not in df.columns or "sector" not in df.columns:
                return set()
            mask = pd.to_numeric(df["limit_up_count"], errors="coerce").fillna(0) >= 1
            return set(s for s in df.loc[mask, "sector"].astype(str).str.strip() if s)
        except Exception:
            return set()

    @staticmethod
    def _load_json(path: str) -> dict:
        p = _project_path(path)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _load_pool(path: str) -> pd.DataFrame:
        p = _project_path(path)
        if not p.exists():
            return pd.DataFrame()
        try:
            return pd.read_csv(p)
        except Exception:
            return pd.DataFrame()
