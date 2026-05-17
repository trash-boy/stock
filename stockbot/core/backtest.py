"""Daily backtester with T-1 signal and T+1 open execution."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stockbot.core.models import AccountSnapshot, Position, Side
from stockbot.strategies.dragon_head import DragonHeadStrategy
from stockbot.strategies.leader import LeaderStrategy

try:
    from stockbot.strategies.dragon_head_exit import DragonHeadExitStrategy
except Exception:
    DragonHeadExitStrategy = None  # type: ignore


@dataclass
class BacktestResult:
    equity: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict[str, Any]


class Backtester:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.initial_cash = float(cfg.get("backtest", {}).get("initial_cash", cfg.get("paper", {}).get("initial_cash", 100000)))
        self.fee_rate = float(cfg.get("backtest", {}).get("fee_rate", 0.0003))
        self.slippage = float(cfg.get("execution", {}).get("price_slippage_pct", 0.003))
        self.lot_size = int(cfg.get("execution", {}).get("lot_size", 100))
        strategy_name = cfg.get("runtime", {}).get("strategy", "dragon_head")
        self.strategy = DragonHeadStrategy(cfg) if strategy_name == "dragon_head" else LeaderStrategy(cfg)
        # 可选:v1.1 龙头式卖出
        self.exit_strategy = None
        if DragonHeadExitStrategy and (cfg.get("dragon_head_exit", {}) or {}).get("enabled"):
            try:
                self.exit_strategy = DragonHeadExitStrategy(cfg)
            except Exception:
                self.exit_strategy = None

    def run(self, bars_by_symbol: dict[str, pd.DataFrame]) -> BacktestResult:
        prepared = self._prepare(bars_by_symbol)
        if not prepared:
            return BacktestResult(pd.DataFrame(), pd.DataFrame(), {"error": "no_data"})
        dates = sorted(set().union(*[set(df.index) for df in prepared.values()]))
        cash = self.initial_cash
        positions: dict[str, dict[str, float]] = {}
        equity_rows: list[dict] = []
        trade_rows: list[dict] = []
        closed_returns: list[float] = []
        risk = self.cfg["risk"]
        max_positions = int(risk["max_positions"])
        max_single_pct = float(risk["max_single_position_pct"])
        max_total_pct = float(risk["max_total_position_pct"])
        stop_loss = float(risk["stop_loss_pct"])
        take_profit = float(risk["take_profit_pct"])
        trailing_stop = float(risk.get("trailing_stop_pct", 0) or 0)

        for idx in range(61, len(dates)):
            signal_date = dates[idx - 1]
            exec_date = dates[idx]
            signal_bars = {s: df.loc[:signal_date].tail(120) for s, df in prepared.items() if signal_date in df.index and len(df.loc[:signal_date]) >= 20}
            exec_rows = {s: df.loc[exec_date] for s, df in prepared.items() if exec_date in df.index}
            close_prices = {s: float(row["close"]) for s, row in exec_rows.items()}
            open_prices = {s: float(row["open"]) for s, row in exec_rows.items()}

            # v1.1: 龙头式卖出优先(emergency / break_board / theme_dead / momentum_failure)
            covered_by_dragon: set[str] = set()
            if self.exit_strategy is not None and positions:
                snap = AccountSnapshot(
                    cash=cash,
                    total_asset=cash + sum(pos["quantity"] * close_prices.get(s, pos["last_price"]) for s, pos in positions.items()),
                    positions={
                        s: Position(symbol=s, quantity=int(pos["quantity"]),
                                    avg_price=float(pos["avg_price"]),
                                    last_price=float(open_prices.get(s, pos["last_price"])),
                                    high_price=float(pos.get("high_price", pos["avg_price"])))
                        for s, pos in positions.items()
                    },
                    daily_pnl_pct=0.0,
                )
                # 用 signal_date 的 bars 重建 strategy 的 dragon_pool 视角(已在策略 generate 阶段做过)
                # 这里直接调 generate_sells;exit_strategy 持有的是构造时的全局 pool — 回测里需要刷新
                self.exit_strategy.dragon_pool = self._point_in_time_dragon_pool(signal_bars)
                self.exit_strategy._sectors_with_new_limit_up = self.exit_strategy._sectors_with_today_lu()
                # 临时注入回测当日 phase
                phase_inject = self._infer_phase_from_bars(signal_bars)
                self.exit_strategy.market_context = {"emotion": {"phase": phase_inject}}
                try:
                    dragon_sells = self.exit_strategy.generate_sells(snap, signal_bars)
                except Exception:
                    dragon_sells = []
                for o in dragon_sells:
                    if o.symbol not in positions or o.symbol not in open_prices:
                        continue
                    pos = positions[o.symbol]
                    qty = min(int(o.quantity), int(pos["quantity"]))
                    if qty <= 0:
                        continue
                    price = open_prices[o.symbol]
                    value = self._sell_value(qty, price)
                    cash += value
                    pnl_pct = price / pos["avg_price"] - 1
                    closed_returns.append(pnl_pct)
                    trade_rows.append(self._trade_row(exec_date, o.symbol, Side.SELL, qty, price,
                                                     o.reason.replace("dragon_exit:", "")))
                    if qty >= int(pos["quantity"]):
                        positions.pop(o.symbol)
                        covered_by_dragon.add(o.symbol)
                    else:
                        pos["quantity"] = int(pos["quantity"]) - qty

            # T+1 open exits(通用止损,跳过已被龙头式覆盖的票)
            for symbol in list(positions):
                if symbol in covered_by_dragon or symbol not in open_prices:
                    continue
                price = open_prices[symbol]
                pos = positions[symbol]
                high_price = max(float(pos.get("high_price", pos["avg_price"])), price)
                pos["high_price"] = high_price
                pnl_pct = price / pos["avg_price"] - 1
                trailing_hit = trailing_stop > 0 and high_price > pos["avg_price"] and price / high_price - 1 <= -trailing_stop
                if pnl_pct <= -stop_loss or pnl_pct >= take_profit or trailing_hit:
                    value = self._sell_value(pos["quantity"], price)
                    cash += value
                    closed_returns.append(pnl_pct)
                    reason = "stop_loss" if pnl_pct <= -stop_loss else "take_profit" if pnl_pct >= take_profit else "trailing_stop"
                    trade_rows.append(self._trade_row(exec_date, symbol, Side.SELL, pos["quantity"], price, reason))
                    positions.pop(symbol)

            total_asset = cash + sum(pos["quantity"] * close_prices.get(s, pos["last_price"]) for s, pos in positions.items())
            if isinstance(self.strategy, DragonHeadStrategy):
                if bool(self.cfg.get("backtest", {}).get("use_static_dragon_pool", False)):
                    self.strategy.set_trade_date(signal_date)
                else:
                    self.strategy.set_runtime_pool(self._point_in_time_dragon_pool(signal_bars))
            signals = self.strategy.generate(signal_bars)
            for signal in signals:
                symbol = signal.symbol
                if symbol in positions or symbol not in exec_rows or len(positions) >= max_positions:
                    continue
                row = exec_rows[symbol]
                if self._cannot_buy(row):
                    continue
                current_position_value = sum(pos["quantity"] * close_prices.get(s, pos["last_price"]) for s, pos in positions.items())
                if total_asset > 0 and current_position_value / total_asset >= max_total_pct:
                    break
                price = open_prices[symbol]
                target_amount = min(total_asset * max_single_pct * signal.weight, cash * 0.95)
                qty = int(target_amount // (price * self.lot_size)) * self.lot_size
                if qty <= 0:
                    continue
                cost = self._buy_cost(qty, price)
                if cost > cash:
                    continue
                cash -= cost
                avg_price = price * (1 + self.slippage)
                positions[symbol] = {"quantity": qty, "avg_price": avg_price, "last_price": close_prices.get(symbol, price), "high_price": max(avg_price, close_prices.get(symbol, price))}
                trade_rows.append(self._trade_row(exec_date, symbol, Side.BUY, qty, price, f"T-1:{signal.reason}"))

            position_value = 0.0
            for symbol, pos in positions.items():
                pos["last_price"] = close_prices.get(symbol, pos["last_price"])
                pos["high_price"] = max(float(pos.get("high_price", pos["last_price"])), pos["last_price"])
                position_value += pos["quantity"] * pos["last_price"]
            equity_rows.append({"date": exec_date.date().isoformat(), "cash": cash, "position_value": position_value, "total_asset": cash + position_value, "positions": len(positions)})

        equity = pd.DataFrame(equity_rows)
        trades = pd.DataFrame(trade_rows)
        metrics = self._metrics(equity, trades, closed_returns)
        return BacktestResult(equity, trades, metrics)

    def save(self, result: BacktestResult, output_dir: str | Path) -> dict[str, str]:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        equity_path = output / "equity.csv"
        trades_path = output / "trades.csv"
        metrics_path = output / "metrics.json"
        result.equity.to_csv(equity_path, index=False, encoding="utf-8-sig")
        result.trades.to_csv(trades_path, index=False, encoding="utf-8-sig")
        metrics_path.write_text(json.dumps(result.metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"equity": str(equity_path), "trades": str(trades_path), "metrics": str(metrics_path)}

    def _prepare(self, bars_by_symbol: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        prepared = {}
        for symbol, df in bars_by_symbol.items():
            if df is None or len(df) < 80:
                continue
            data = df.copy()
            data["date"] = pd.to_datetime(data["date"])
            data = data.sort_values("date").set_index("date")
            prepared[symbol] = data
        return prepared



    @staticmethod
    def _infer_phase_from_bars(signal_bars: dict) -> str:
        """按当日所有标的的涨跌分布,简化推断 phase。

        - limit_up_count > down_count*2: ferment(发酵)
        - limit_up_count >= down_count: repair
        - down_count > limit_up_count*3: panic
        - 介于 panic 和 repair 之间: cooldown
        - 找不到数据: unknown
        """
        if not signal_bars:
            return "unknown"
        lu = down = total = 0
        for sym, df in signal_bars.items():
            if df is None or df.empty:
                continue
            try:
                pct = float(df.iloc[-1].get("pct_change", 0) or 0)
            except Exception:
                continue
            total += 1
            threshold = Backtester._limit_up_threshold(sym)
            if pct >= threshold:
                lu += 1
            elif pct <= -9.5:
                down += 1
        if total == 0:
            return "unknown"
        if lu == 0 and down == 0:
            return "repair"
        if lu > down * 2 and lu >= 3:
            return "ferment"
        if down > lu * 3 and down >= 5:
            return "panic"
        if down > lu and down >= 3:
            return "cooldown"
        return "repair"

    @staticmethod
    def _point_in_time_dragon_pool(signal_bars: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """Build a same-day涨停池 approximation from bars visible at signal_date.

        This avoids the backtest reading today's static dragon_pool.csv for past
        dates. It keeps only data available at the signal date; intraday fields
        like seal amount are set conservatively when unavailable.
        """
        rows: list[dict[str, Any]] = []
        for symbol, df in signal_bars.items():
            if df is None or df.empty:
                continue
            latest = df.iloc[-1]
            pct = float(latest.get("pct_change", 0) or 0)
            close = float(latest.get("close", 0) or 0)
            high = float(latest.get("high", close) or close)
            amount = float(latest.get("amount", 0) or 0)
            threshold = Backtester._limit_up_threshold(symbol)
            if pct < threshold or close <= 0:
                continue
            streak = 0
            for x in reversed(pd.to_numeric(df.get("pct_change", pd.Series(dtype=float)).tail(10), errors="coerce").fillna(0).tolist()):
                if float(x) >= threshold:
                    streak += 1
                else:
                    break
            rows.append({
                "symbol": symbol,
                "name": str(latest.get("name", "")),
                "sector": str(latest.get("sector", "未分组")) or "未分组",
                "limit_up_count": max(streak, 1),
                "score": pct,
                "price": close,
                "pct_change": pct,
                "amount": amount,
                "seal_amount": float(latest.get("seal_amount", 0) or 0),
                "turnover": float(latest.get("turnover", 0) or 0),
                "first_limit_time": float(latest.get("first_limit_time", 999999) or 999999),
                "last_limit_time": float(latest.get("last_limit_time", 999999) or 999999),
                "open_times": int(float(latest.get("open_times", 0) or 0)),
                "high": high,
            })
        return pd.DataFrame(rows)

    @staticmethod
    def _limit_up_threshold(symbol: str) -> float:
        code = str(symbol).split(".", 1)[0]
        return 19.5 if code.startswith(("300", "301", "688")) else 9.5

    @staticmethod
    def _cannot_buy(row: pd.Series) -> bool:
        pct = float(row.get("pct_change", 0) or 0)
        open_ = float(row.get("open", 0) or 0)
        high = float(row.get("high", 0) or 0)
        low = float(row.get("low", 0) or 0)
        symbol = str(row.get("symbol", ""))
        threshold = Backtester._limit_up_threshold(symbol)
        # 一字/极端涨停无法按 open 稳定成交；跌停也不买。
        return pct >= 19.8 or pct <= -9.5 or (high <= low and pct >= threshold) or open_ <= 0

    def _buy_cost(self, quantity: int, price: float) -> float:
        gross = quantity * price * (1 + self.slippage)
        return gross * (1 + self.fee_rate)

    def _sell_value(self, quantity: int, price: float) -> float:
        gross = quantity * price * (1 - self.slippage)
        return gross * (1 - self.fee_rate)

    def _trade_row(self, date, symbol: str, side: Side, quantity: int, price: float, reason: str) -> dict:
        return {"date": str(date.date() if hasattr(date, "date") else date), "symbol": symbol, "side": side.value, "quantity": quantity, "price": round(price, 4), "amount": round(quantity * price, 2), "reason": reason}

    def _metrics(self, equity: pd.DataFrame, trades: pd.DataFrame, closed_returns: list[float]) -> dict[str, Any]:
        if equity.empty:
            return {"error": "empty_equity"}
        start = float(equity.iloc[0]["total_asset"])
        end = float(equity.iloc[-1]["total_asset"])
        curve = equity["total_asset"].astype(float)
        high = curve.cummax()
        drawdown = curve / high - 1
        daily_ret = curve.pct_change().dropna()
        return {
            "initial_asset": round(start, 2),
            "final_asset": round(end, 2),
            "total_return": round(end / start - 1, 4) if start > 0 else 0,
            "max_drawdown": round(float(drawdown.min()), 4),
            "annualized_sharpe": round(float(daily_ret.mean() / daily_ret.std() * (252 ** 0.5)), 4) if len(daily_ret) > 2 and daily_ret.std() > 0 else 0,
            "sharpe_note": "按每日权益收益年化，仅用于同策略横向比较；胜率使用已平仓回合统计。",
            "trade_count": int(len(trades)),
            "buy_count": int((trades["side"] == "BUY").sum()) if not trades.empty else 0,
            "sell_count": int((trades["side"] == "SELL").sum()) if not trades.empty else 0,
            "closed_rounds": len(closed_returns),
            "win_rate": round(sum(1 for x in closed_returns if x > 0) / len(closed_returns), 4) if closed_returns else 0,
            "avg_closed_return": round(sum(closed_returns) / len(closed_returns), 4) if closed_returns else 0,
        }
