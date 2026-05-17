"""Daily trading and risk report generation."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReportPaths:
    markdown: Path
    json: Path


class DailyReport:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.trade_log = Path(cfg.get("logging", {}).get("trade_log", "stockbot/logs/trades.csv"))
        self.reject_log = Path(cfg.get("logging", {}).get("reject_log", "stockbot/logs/rejections.csv"))
        self.paper_state = Path(cfg.get("paper", {}).get("state_path", "stockbot/data/paper_account.json"))
        self.output_dir = Path(cfg.get("report", {}).get("output_dir", "stockbot/reports"))

    def generate(self, date: str | None = None) -> ReportPaths:
        date = date or datetime.now().date().isoformat()
        trades = self._read_csv_for_date(self.trade_log, date)
        rejections = self._read_csv_for_date(self.reject_log, date)
        account = self._read_account()
        metrics = self._metrics(trades, rejections, account)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        md_path = self.output_dir / f"daily_report_{date}.md"
        json_path = self.output_dir / f"daily_report_{date}.json"
        md_path.write_text(self._render_markdown(date, trades, rejections, account, metrics), encoding="utf-8")
        json_path.write_text(
            json.dumps(
                {"date": date, "metrics": metrics, "account": account, "trades": trades, "rejections": rejections},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return ReportPaths(markdown=md_path, json=json_path)

    def _read_csv_for_date(self, path: Path, date: str) -> list[dict[str, str]]:
        if not path.exists():
            return []
        rows: list[dict[str, str]] = []
        with path.open("r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row = dict(row)
                if row.get("time", "").startswith(date):
                    rows.append(row)
        return rows

    def _read_account(self) -> dict[str, Any]:
        if not self.paper_state.exists():
            return {"cash": 0.0, "positions": {}, "orders": []}
        try:
            return json.loads(self.paper_state.read_text(encoding="utf-8"))
        except Exception:
            return {"cash": 0.0, "positions": {}, "orders": [], "warning": "paper_account_json_invalid"}

    def _metrics(self, trades: list[dict[str, str]], rejections: list[dict[str, str]], account: dict[str, Any]) -> dict[str, Any]:
        cash = float(account.get("cash", 0) or 0)
        positions = account.get("positions", {}) or {}
        position_value = 0.0
        unrealized_pnl = 0.0
        for pos in positions.values():
            qty = int(pos.get("quantity", 0) or 0)
            avg_price = float(pos.get("avg_price", 0) or 0)
            last_price = float(pos.get("last_price", avg_price) or 0)
            position_value += qty * last_price
            unrealized_pnl += qty * (last_price - avg_price)
        buy_amount = sum(float(x.get("amount", 0) or 0) for x in trades if x.get("side") == "BUY")
        sell_amount = sum(float(x.get("amount", 0) or 0) for x in trades if x.get("side") == "SELL")
        total_asset = cash + position_value
        return {
            "trade_count": len(trades),
            "rejection_count": len(rejections),
            "buy_amount": round(buy_amount, 2),
            "sell_amount": round(sell_amount, 2),
            "cash": round(cash, 2),
            "position_count": len(positions),
            "position_value": round(position_value, 2),
            "total_asset": round(total_asset, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "position_pct": round(position_value / total_asset, 4) if total_asset > 0 else 0.0,
        }

    def _render_markdown(
        self,
        date: str,
        trades: list[dict[str, str]],
        rejections: list[dict[str, str]],
        account: dict[str, Any],
        metrics: dict[str, Any],
    ) -> str:
        lines = [
            f"# StockBot 每日交易与风控日报 - {date}",
            "",
            "## 账户概览",
            "",
            f"- 总资产：{metrics['total_asset']}",
            f"- 现金：{metrics['cash']}",
            f"- 持仓市值：{metrics['position_value']}",
            f"- 仓位比例：{metrics['position_pct']:.2%}",
            f"- 浮动盈亏：{metrics['unrealized_pnl']}",
            f"- 持仓数量：{metrics['position_count']}",
            "",
            "## 今日交易",
            "",
            f"- 成交笔数：{metrics['trade_count']}",
            f"- 买入金额：{metrics['buy_amount']}",
            f"- 卖出金额：{metrics['sell_amount']}",
            "",
        ]
        if trades:
            lines.extend(["| 时间 | 标的 | 方向 | 数量 | 价格 | 金额 | 原因 |", "|---|---|---:|---:|---:|---:|---|"])
            for row in trades:
                lines.append(
                    f"| {row.get('time','')} | {row.get('symbol','')} | {row.get('side','')} | {row.get('quantity','')} | {row.get('price','')} | {row.get('amount','')} | {row.get('reason','')} |"
                )
        else:
            lines.append("今日无成交。")
        lines.extend(["", "## 风控拒单", "", f"- 拒单数量：{metrics['rejection_count']}", ""])
        if rejections:
            lines.extend(["| 时间 | 标的 | 方向 | 数量 | 金额 | 信号原因 | 拒单原因 |", "|---|---|---:|---:|---:|---|---|"])
            for row in rejections:
                lines.append(
                    f"| {row.get('time','')} | {row.get('symbol','')} | {row.get('side','')} | {row.get('quantity','')} | {row.get('amount','')} | {row.get('signal_reason','')} | {row.get('reject_reason','')} |"
                )
        else:
            lines.append("今日无风控拒单。")
        lines.extend(["", "## 当前持仓", ""])
        positions = account.get("positions", {}) or {}
        if positions:
            lines.extend(["| 标的 | 数量 | 成本价 | 最新价 | 市值 | 浮盈亏 |", "|---|---:|---:|---:|---:|---:|"])
            for symbol, pos in positions.items():
                qty = int(pos.get("quantity", 0) or 0)
                avg = float(pos.get("avg_price", 0) or 0)
                last = float(pos.get("last_price", avg) or 0)
                lines.append(f"| {symbol} | {qty} | {avg:.3f} | {last:.3f} | {qty * last:.2f} | {qty * (last - avg):.2f} |")
        else:
            lines.append("当前无持仓。")
        lines.extend([
            "",
            "## 风控状态",
            "",
            f"- 单票仓位上限：{float(self.cfg['risk']['max_single_position_pct']):.0%}",
            f"- 总仓位上限：{float(self.cfg['risk']['max_total_position_pct']):.0%}",
            f"- 单日买入上限：{self.cfg['risk']['max_daily_buy_amount']}",
            f"- 单笔订单上限：{self.cfg['risk']['max_single_order_amount']}",
            f"- 止损线：{float(self.cfg['risk']['stop_loss_pct']):.0%}",
            f"- 止盈线：{float(self.cfg['risk']['take_profit_pct']):.0%}",
            f"- 移动止盈回撤线：{float(self.cfg['risk'].get('trailing_stop_pct', 0)):.0%}",
            "",
            "> 本报告基于本地 paper 账户和交易日志生成，不构成投资建议。",
            "",
        ])
        return "\n".join(lines)
