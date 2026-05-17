from pathlib import Path
root = Path('/Users/bytedance/PycharmProjects/stock')

# 1) scripts/build_dragon_pool.py: project path, fallback from universe, never silently leave missing/empty pool.
p = root / 'scripts/build_dragon_pool.py'
p.write_text('''"""Build 龙头候选池 from limit-up pool and sector heat.

Production invariant: stockbot/data/dragon_pool.csv must exist and contain a
usable schema before DragonHeadStrategy runs. If AkShare's zt pool is down, this
script falls back to current universe strong stocks so the strategy can still
score candidates instead of returning zero forever.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from stockbot.core.config import load_config

REQUIRED_COLUMNS = [
    "symbol", "name", "sector", "limit_up_count", "score", "price", "pct_change", "amount",
    "seal_amount", "turnover", "first_limit_time", "last_limit_time", "open_times",
]


def project_path(path_text: str | Path) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else PROJECT_ROOT / path


def normalize_symbol(code: str) -> str:
    code = str(code).strip().lower().replace("sh", "").replace("sz", "").replace("bj", "").split(".", 1)[0].zfill(6)
    return f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.SZ"


def fetch_zt_pool(date: str) -> pd.DataFrame:
    import akshare as ak
    try:
        return ak.stock_zt_pool_em(date=date)
    except TypeError:
        return ak.stock_zt_pool_em()


def build_pool(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return empty_pool()
    df = raw.rename(
        columns={
            "代码": "code", "名称": "name", "涨跌幅": "pct_change", "最新价": "price", "成交额": "amount",
            "流通市值": "float_market_cap", "总市值": "market_cap", "换手率": "turnover", "封板资金": "seal_amount",
            "首次封板时间": "first_limit_time", "最后封板时间": "last_limit_time", "炸板次数": "open_times",
            "连板数": "limit_up_count", "所属行业": "sector",
        }
    ).copy()
    for col in ["code", "name", "sector"]:
        if col not in df.columns:
            df[col] = ""
    for col in ["price", "amount", "seal_amount", "turnover", "pct_change", "limit_up_count", "open_times"]:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["symbol"] = df["code"].map(normalize_symbol)
    df["limit_up_count"] = df["limit_up_count"].clip(lower=1)
    df["first_limit_time_num"] = pd.to_numeric(df.get("first_limit_time", 999999), errors="coerce").fillna(999999)
    amount_rank = df["amount"].rank(pct=True)
    seal_rank = df["seal_amount"].rank(pct=True)
    ladder = df["limit_up_count"].clip(0, 6) / 6
    early = 1 - (df["first_limit_time_num"].rank(pct=True) * 0.6)
    open_penalty = (df["open_times"].clip(0, 5) / 5) * 0.2
    df["score"] = ladder * 0.40 + seal_rank * 0.25 + amount_rank * 0.20 + early * 0.15 - open_penalty
    return normalize_pool_schema(df)


def build_fallback_pool(universe_file: str | Path, top: int) -> pd.DataFrame:
    path = project_path(universe_file)
    if not path.exists():
        return empty_pool()
    df = pd.read_csv(path)
    if df.empty:
        return empty_pool()
    if "symbol" not in df.columns:
        code_col = "code" if "code" in df.columns else "代码" if "代码" in df.columns else None
        if code_col is None:
            return empty_pool()
        df["symbol"] = df[code_col].map(normalize_symbol)
    rename = {"名称": "name", "最新价": "price", "涨跌幅": "pct_change", "成交额": "amount", "换手率": "turnover", "所属行业": "sector"}
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns and v not in df.columns}).copy()
    for col in ["name", "sector"]:
        if col not in df.columns:
            df[col] = ""
    for col in ["price", "pct_change", "amount", "turnover"]:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    # Fallback only takes strong candidates. It is not a replacement for real zt pool,
    # but prevents an absent file from making production path permanently zero-signal.
    strong = df[df["pct_change"] >= 5.0].copy()
    if strong.empty:
        return empty_pool()
    strong = strong.sort_values(["pct_change", "amount"], ascending=[False, False]).head(top)
    strong["limit_up_count"] = (strong["pct_change"] >= 9.5).astype(int).clip(lower=1)
    strong["seal_amount"] = 0.0
    strong["first_limit_time"] = 999999
    strong["last_limit_time"] = 999999
    strong["open_times"] = 0
    amount_rank = strong["amount"].rank(pct=True)
    pct_score = strong["pct_change"].clip(0, 20) / 20
    strong["score"] = pct_score * 0.65 + amount_rank * 0.35
    return normalize_pool_schema(strong)


def empty_pool() -> pd.DataFrame:
    return pd.DataFrame(columns=REQUIRED_COLUMNS)


def normalize_pool_schema(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    for col in REQUIRED_COLUMNS:
        if col not in result.columns:
            result[col] = "" if col in {"symbol", "name", "sector", "first_limit_time", "last_limit_time"} else 0
    result = result[REQUIRED_COLUMNS]
    return result.sort_values("score", ascending=False)


def save_pool(result: pd.DataFrame, output: str | Path) -> Path:
    path = project_path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--output", default="stockbot/data/dragon_pool.csv")
    parser.add_argument("--universe-file", default="stockbot/data/universe.csv")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--allow-fallback", action="store_true")
    args = parser.parse_args()
    load_config(args.config)

    source = "zt_pool"
    try:
        result = build_pool(fetch_zt_pool(args.date))
    except Exception as exc:
        print(f"warn: zt_pool failed: {exc}")
        result = empty_pool()
    if result.empty and args.allow_fallback:
        source = "universe_fallback"
        result = build_fallback_pool(args.universe_file, args.top)
    path = save_pool(result, args.output)
    print(f"dragon_pool_size={len(result)} source={source} output={path}")
    for _, row in result.head(args.top).iterrows():
        print(f"{row['symbol']} {row['name']} sector={row['sector']} limit={row['limit_up_count']} score={round(float(row['score']),4)} amount={int(float(row['amount'] or 0))}")
    if result.empty:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''', encoding='utf-8')

# 2) dragon_head.py: if pool file is absent/empty, derive runtime pool from bars instead of all None.
p = root / 'stockbot/strategies/dragon_head.py'
text = p.read_text(encoding='utf-8')
text = text.replace('''        ranked: list[dict] = []\n        for symbol, df in bars_by_symbol.items():\n''', '''        self._ensure_pool_from_bars(bars_by_symbol)\n        ranked: list[dict] = []\n        for symbol, df in bars_by_symbol.items():\n''')
insert = '''\n    def _ensure_pool_from_bars(self, bars_by_symbol: dict[str, object]) -> None:\n        """Last-resort production guard: derive a candidate pool from loaded bars.\n\n        build_dragon_pool.py is still the primary source. This guard prevents an\n        absent/empty CSV from making _pool_row return None for every symbol.\n        """\n        if not self.dragon_pool.empty:\n            return\n        rows: list[dict] = []\n        for symbol, df in bars_by_symbol.items():\n            if df is None or len(df) == 0:\n                continue\n            latest = df.iloc[-1]\n            pct = float(latest.get("pct_change", 0) or 0)\n            if pct < 5.0:\n                continue\n            amount = float(latest.get("amount", 0) or 0)\n            close = float(latest.get("close", latest.get("price", 0)) or 0)\n            rows.append({\n                "symbol": symbol,\n                "name": str(latest.get("name", "")),\n                "sector": str(latest.get("sector", latest.get("industry", "")) or "未分组"),\n                "limit_up_count": 1,\n                "score": min(max(pct, 0), 20) / 20,\n                "price": close,\n                "pct_change": pct,\n                "amount": amount,\n                "seal_amount": float(latest.get("seal_amount", 0) or 0),\n                "turnover": float(latest.get("turnover", 0) or 0),\n                "first_limit_time": float(latest.get("first_limit_time", 999999) or 999999),\n                "last_limit_time": float(latest.get("last_limit_time", 999999) or 999999),\n                "open_times": int(float(latest.get("open_times", 0) or 0)),\n            })\n        if rows:\n            self.dragon_pool = self._with_sector_rank(pd.DataFrame(rows))\n\n'''
text = text.replace('''    def score_one(self, symbol: str, df: object) -> dict | None:\n''', insert + '    def score_one(self, symbol: str, df: object) -> dict | None:\n')
# allow empty sector all not collapse? current groupby sector ok.
p.write_text(text, encoding='utf-8')

# 3) daily_run.py: add build_dragon_pool step before trader and fail early if unavailable.
p = root / 'scripts/daily_run.py'
text = p.read_text(encoding='utf-8')
needle = '''    if market_context_step["returncode"] != 0:\n        log_path, summary_path = write_log(run_id, steps)\n        print(f"daily_run=FAILED step=build_market_context log={log_path} summary={summary_path}")\n        return market_context_step["returncode"]\n\n    steps.append(\n        run_step(\n            "run_trader_paper",\n'''
replacement = '''    if market_context_step["returncode"] != 0:\n        log_path, summary_path = write_log(run_id, steps)\n        print(f"daily_run=FAILED step=build_market_context log={log_path} summary={summary_path}")\n        return market_context_step["returncode"]\n\n    dragon_pool_step = run_step(\n        "build_dragon_pool",\n        [\n            str(PYTHON),\n            "scripts/build_dragon_pool.py",\n            "--config",\n            args.config,\n            "--universe-file",\n            args.universe_file,\n            "--top",\n            str(args.top),\n            "--allow-fallback",\n        ],\n        min(args.timeout, 180),\n    )\n    steps.append(dragon_pool_step)\n    if dragon_pool_step["returncode"] != 0:\n        log_path, summary_path = write_log(run_id, steps)\n        print(f"daily_run=FAILED step=build_dragon_pool log={log_path} summary={summary_path}")\n        return dragon_pool_step["returncode"]\n\n    steps.append(\n        run_step(\n            "run_trader_paper",\n'''
if needle not in text:
    raise SystemExit('needle not found daily_run')
text = text.replace(needle, replacement)
p.write_text(text, encoding='utf-8')

print('dragon_pool_p0_fixed')
