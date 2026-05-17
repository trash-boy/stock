"""手工确认成交后,把 pending_orders 落到 positions/cash + orders 历史。

用法:
    # 列出待确认订单
    python3 scripts/confirm_manual_fill.py --list

    # 确认第 N 条 pending(从 0 开始) 已成交
    python3 scripts/confirm_manual_fill.py --confirm 0 --price 60.50 --quantity 100

    # 拒绝(没买上)
    python3 scripts/confirm_manual_fill.py --reject 0
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

STATE_PATH = PROJECT_ROOT / "stockbot/data/paper_account.json"


def load_state():
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_list(state):
    pending = state.get("pending_orders", [])
    if not pending:
        print("无 pending_orders")
        return
    for i, o in enumerate(pending):
        print(f"[{i}] {o['intent_at']} {o['side']} {o['symbol']} qty={o['quantity']} price={o['price']} reason={o['reason']} status={o.get('status','?')}")


def cmd_confirm(state, idx, fill_price, fill_qty):
    pending = state.setdefault("pending_orders", [])
    if idx < 0 or idx >= len(pending):
        raise SystemExit(f"index {idx} 越界,共 {len(pending)} 条")
    o = pending[idx]
    if o.get("status") != "pending_manual":
        raise SystemExit(f"该订单状态={o.get('status')},非 pending,跳过")
    fill_price = float(fill_price) if fill_price else float(o["price"])
    fill_qty = int(fill_qty) if fill_qty else int(o["quantity"])
    side = o["side"]
    symbol = o["symbol"]
    amount = fill_qty * fill_price
    positions = state.setdefault("positions", {})
    cash = float(state.get("cash", 0))
    if side == "BUY":
        if amount > cash:
            raise SystemExit(f"现金 {cash} 不足以买 {amount}")
        pos = positions.get(symbol, {"quantity": 0, "avg_price": 0.0, "last_price": fill_price, "high_price": fill_price})
        old_qty = int(pos["quantity"])
        old_cost = old_qty * float(pos["avg_price"])
        new_qty = old_qty + fill_qty
        pos["quantity"] = new_qty
        pos["avg_price"] = (old_cost + amount) / new_qty
        pos["last_price"] = fill_price
        pos["high_price"] = max(float(pos.get("high_price", fill_price)), fill_price, pos["avg_price"])
        positions[symbol] = pos
        state["cash"] = cash - amount
    elif side == "SELL":
        pos = positions.get(symbol)
        if not pos or int(pos.get("quantity", 0)) < fill_qty:
            raise SystemExit(f"持仓不足以卖 {fill_qty} 股")
        pos["quantity"] = int(pos["quantity"]) - fill_qty
        pos["last_price"] = fill_price
        state["cash"] = cash + amount
        if int(pos["quantity"]) <= 0:
            positions.pop(symbol, None)
    o["status"] = "filled"
    o["filled_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    o["fill_price"] = fill_price
    o["fill_quantity"] = fill_qty
    state.setdefault("orders", []).append({
        "executed_at": o["filled_at"],
        "symbol": symbol,
        "side": side,
        "quantity": fill_qty,
        "price": fill_price,
        "amount": round(amount, 2),
        "reason": o["reason"] + " (manual_filled)",
    })
    print(f"OK confirmed [{idx}] {side} {symbol} {fill_qty}@{fill_price} amount={amount:.2f}")


def cmd_reject(state, idx):
    pending = state.setdefault("pending_orders", [])
    if idx < 0 or idx >= len(pending):
        raise SystemExit(f"index {idx} 越界")
    o = pending[idx]
    o["status"] = "rejected"
    o["rejected_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    print(f"OK rejected [{idx}] {o['side']} {o['symbol']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--confirm", type=int, help="pending_orders index")
    parser.add_argument("--reject", type=int, help="pending_orders index")
    parser.add_argument("--price", type=float, help="实际成交价(可选,默认用信号价)")
    parser.add_argument("--quantity", type=int, help="实际成交数量(可选,默认用信号数量)")
    args = parser.parse_args()

    state = load_state()
    if args.list or (args.confirm is None and args.reject is None):
        cmd_list(state)
        return
    if args.confirm is not None:
        cmd_confirm(state, args.confirm, args.price, args.quantity)
    if args.reject is not None:
        cmd_reject(state, args.reject)
    save_state(state)


if __name__ == "__main__":
    main()
