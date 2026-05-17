"""Generate StockBot daily trading/risk report and (optionally) push to Lark."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stockbot.core.config import load_config
from stockbot.core.report import DailyReport


def _push_lark_summary(cfg: dict, md_path: Path) -> None:
    webhook = (cfg.get("lark", {}) or {}).get("webhook", "").strip()
    if not webhook:
        return
    try:
        text = md_path.read_text(encoding="utf-8")
        # 截断,飞书机器人单条消息有上限;留尾以便看到风险/订单
        if len(text) > 3500:
            text = text[:1500] + "\n... [truncated] ...\n" + text[-1500:]
        body = json.dumps(
            {"msg_type": "text", "content": {"text": f"📊 stockbot daily report\n{text}"}}
        ).encode("utf-8")
        req = urllib.request.Request(webhook, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=8).read()
        print("lark_summary=pushed")
    except Exception as e:
        print(f"lark_summary=failed err={e}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--date", default="")
    parser.add_argument("--no-lark", action="store_true", help="不推送 Lark 摘要")
    args = parser.parse_args()
    cfg = load_config(args.config)
    paths = DailyReport(cfg).generate(args.date or None)
    print(f"report_md={paths.markdown}")
    print(f"report_json={paths.json}")
    if not args.no_lark:
        _push_lark_summary(cfg, Path(paths.markdown))


if __name__ == "__main__":
    main()
