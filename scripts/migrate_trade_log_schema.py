"""One-time migration for old trades.csv without amount column."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HEADER = ["time", "symbol", "side", "quantity", "price", "amount", "reason"]


def project_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else PROJECT_ROOT / path


def migrate(path: Path) -> Path | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    with path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows:
        return None
    if rows[0] == HEADER:
        return None
    backup = path.with_name(f"{path.stem}.legacy_{datetime.now().strftime('%Y%m%d_%H%M%S')}{path.suffix}")
    path.replace(backup)
    migrated = []
    old_header = rows[0]
    for raw in rows[1:]:
        if not raw:
            continue
        record = dict(zip(old_header, raw))
        extra = raw[len(old_header):]
        time = record.get("time", "")
        symbol = record.get("symbol", "")
        side = record.get("side", "")
        quantity = record.get("quantity", "0")
        price = record.get("price", "0")
        reason = record.get("reason", "")
        amount = record.get("amount", "")
        if extra:
            if not amount:
                amount = reason
            reason = extra[-1]
        if not amount:
            try:
                amount = str(round(float(quantity or 0) * float(price or 0), 2))
            except Exception:
                amount = "0"
        migrated.append([time, symbol, side, quantity, price, amount, reason])
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        writer.writerows(migrated)
    return backup


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="stockbot/logs/trades.csv")
    args = parser.parse_args()
    backup = migrate(project_path(args.path))
    print(f"migrated=true backup={backup}" if backup else "migrated=false")


if __name__ == "__main__":
    main()
