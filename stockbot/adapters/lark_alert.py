"""Lark/Feishu alert broker.

This is a "fake broker" — it does NOT place real orders. Instead it pushes the
order intent to a Lark webhook so the user can manually execute the trade in
their broker app. Local paper account state is reused as the source of truth
for positions/cash, identical to PaperBroker.
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from stockbot.core.models import AccountSnapshot, OrderIntent, Position, Side

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _project_path(path_text):
    p = Path(path_text)
    return p if p.is_absolute() else PROJECT_ROOT / p


class LarkAlertBroker:
    """Pushes order intents to Lark webhook; reuses paper_account.json for state.

    Config schema:
        lark:
          webhook: "https://open.larksuite.com/open-apis/bot/v2/hook/xxx"
          secret: ""              # optional, for signed bots
          mention_user: ""        # optional open_id for @mention
        paper:
          state_path: stockbot/data/paper_account.json
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        lark_cfg = cfg.get("lark", {})
        self.webhook = lark_cfg.get("webhook", "").strip()
        self.secret = lark_cfg.get("secret", "").strip()
        self.mention_user = lark_cfg.get("mention_user", "").strip()
        # webhook 未配置时降级为 dry-run 日志模式:不报错,但所有"推送"只打到 stderr
        # 这让 pipeline 即使在 webhook 缺失时也能跑通,便于联调
        self.dry_run = not self.webhook
        if self.dry_run:
            import sys as _sys
            print("[LarkAlertBroker] WARN webhook 未配置,进入 dry-run 模式 (信号只入 pending_orders,不推送)", file=_sys.stderr, flush=True)
        paper_cfg = cfg.get("paper", {})
        self.state_path = _project_path(paper_cfg.get("state_path", "stockbot/data/paper_account.json"))
        self.initial_cash = float(paper_cfg.get("initial_cash", 100000))
        self._state = self._load_state()
        self._migrate_state()

    # ---------------- state (复用 paper 账本) ----------------
    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"cash": self.initial_cash, "positions": {}, "orders": []}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _migrate_state(self) -> None:
        changed = False
        for pos in self._state.setdefault("positions", {}).values():
            avg = float(pos.get("avg_price", 0) or 0)
            last = float(pos.get("last_price", avg) or avg)
            if "high_price" not in pos:
                pos["high_price"] = max(avg, last)
                changed = True
        if changed:
            self._save_state()

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    @contextmanager
    def _locked_state(self):
        lock_path = self.state_path.with_suffix(self.state_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    # ---------------- broker interface ----------------
    def snapshot(self) -> AccountSnapshot:
        positions: dict[str, Position] = {}
        for symbol, raw in self._state.get("positions", {}).items():
            qty = int(raw.get("quantity", 0))
            if qty <= 0:
                continue
            avg_price = float(raw.get("avg_price", 0) or 0)
            last_price = float(raw.get("last_price", avg_price) or avg_price)
            high_price = float(raw.get("high_price", max(avg_price, last_price)) or max(avg_price, last_price))
            positions[symbol] = Position(symbol, qty, avg_price, last_price, high_price)
        cash = float(self._state.get("cash", 0))
        total_asset = cash + sum(p.market_value for p in positions.values())
        return AccountSnapshot(cash=cash, total_asset=total_asset, positions=positions)

    def mark_price(self, symbol: str, price: float) -> None:
        if price <= 0:
            return
        pos = self._state.setdefault("positions", {}).get(symbol)
        if pos:
            avg = float(pos.get("avg_price", price) or price)
            prev_high = float(pos.get("high_price", max(avg, price)) or max(avg, price))
            pos["last_price"] = float(price)
            pos["high_price"] = max(prev_high, float(price))
            self._save_state()

    def place_order(self, order: OrderIntent) -> int:
        """Push to Lark + record as 'pending_manual' in local state.

        We DO NOT update positions/cash — that requires user confirmation that
        the trade actually executed. Use scripts/confirm_manual_fill.py to mark
        the order as filled after manual execution.
        """
        import sys as _sys
        with self._locked_state():
            self._state = self._load_state()
            self._migrate_state()
            self._expire_old_pending()
            # 同票去重(已持仓时拒绝 BUY)
            if order.side == Side.BUY:
                existing = self._state.get("positions", {}).get(order.symbol, {})
                if int(existing.get("quantity", 0) or 0) > 0:
                    raise RuntimeError(f"reject duplicate BUY: {order.symbol} already held")
                # pending 中相同标的相同方向也去重(避免重复推送)
                for po in self._state.get("pending_orders", []):
                    if po.get("status") == "pending_manual" and po.get("symbol") == order.symbol and po.get("side") == "BUY":
                        raise RuntimeError(f"reject duplicate BUY: {order.symbol} already pending")
            # 推送
            push_ok = self._push_to_lark(order)
            if not push_ok:
                # 让 daily_run 日志可以 grep 到推送失败
                print(f"[LarkAlertBroker] WARN push_failed symbol={order.symbol} side={order.side.value}", file=_sys.stderr, flush=True)
            # 记录为 pending,等手工确认成交
            self._state.setdefault("pending_orders", []).append({
                "intent_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "symbol": order.symbol,
                "side": order.side.value,
                "quantity": order.quantity,
                "price": order.price,
                "amount": round(order.quantity * order.price, 2),
                "reason": order.reason,
                "push_ok": push_ok,
                "status": "pending_manual",
            })
            self._save_state()
            return len(self._state["pending_orders"])

    def _expire_old_pending(self, max_age_days: int = 1) -> None:
        """T+1 自动过期: 上一交易日及更早未确认的 pending_orders 标记 expired。"""
        from datetime import timedelta as _td
        cutoff = datetime.now() - _td(days=max_age_days)
        changed = False
        for po in self._state.get("pending_orders", []):
            if po.get("status") != "pending_manual":
                continue
            try:
                t = datetime.strptime(po.get("intent_at", ""), "%Y-%m-%dT%H:%M:%S")
            except Exception:
                continue
            if t < cutoff:
                po["status"] = "expired"
                changed = True
        if changed:
            # 不立刻 save,_save_state 会在外层调用前完成
            pass

    # ---------------- Lark push ----------------
    def _push_to_lark(self, order: OrderIntent) -> bool:
        if self.dry_run:
            import sys as _sys
            print(f"[LarkAlertBroker] dry_run signal symbol={order.symbol} side={order.side.value} qty={order.quantity} price={order.price} reason={order.reason}", file=_sys.stderr, flush=True)
            return True
        side_emoji = "🚀 买入" if order.side == Side.BUY else "💰 卖出"
        lines = [
            f"{side_emoji} 信号 — 龙头战法",
            f"代码: {order.symbol}",
            f"数量: {order.quantity} 股",
            f"价格: ¥{order.price:.2f}",
            f"金额: ¥{round(order.quantity * order.price, 2):,.0f}",
            f"理由: {order.reason}",
            f"时间: {datetime.now().strftime('%H:%M:%S')}",
            "请于 14:55 前手动执行",
        ]
        text = "\n".join(lines)
        if self.mention_user:
            text = f'<at user_id="{self.mention_user}"></at> ' + text
        payload = {"msg_type": "text", "content": {"text": text}}
        if self.secret:
            payload.update(self._sign_payload())
        try:
            req = urllib.request.Request(
                self.webhook,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", "ignore")
                if resp.status == 200 and ('"code":0' in body or '"StatusCode":0' in body):
                    return True
                print(f"warn: lark push non-zero: status={resp.status} body={body[:200]}")
                return False
        except urllib.error.URLError as exc:
            print(f"warn: lark push failed: {exc}")
            return False

    def _sign_payload(self) -> dict:
        import base64
        import hashlib
        import hmac
        import time
        ts = str(int(time.time()))
        string_to_sign = f"{ts}\n{self.secret}"
        sign = base64.b64encode(
            hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        ).decode("utf-8")
        return {"timestamp": ts, "sign": sign}
