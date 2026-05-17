"""Build 龙头候选池 from limit-up pool and sector heat.

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


def fetch_yesterday_zt(date_str: str) -> pd.DataFrame:
    """拉昨日涨停股池,用于计算"昨日涨停今日表现"晋级率。"""
    import akshare as ak
    from datetime import datetime, timedelta
    try:
        d = datetime.strptime(date_str, "%Y%m%d")
    except Exception:
        return pd.DataFrame()
    # 找上一个工作日(简化:weekday<5)
    prev = d - timedelta(days=1)
    for _ in range(7):
        if prev.weekday() < 5:
            break
        prev -= timedelta(days=1)
    try:
        return ak.stock_zt_pool_em(date=prev.strftime("%Y%m%d"))
    except Exception as exc:
        print(f"warn: fetch_yesterday_zt failed: {exc}")
        return pd.DataFrame()


def compute_promotion_stats(yesterday: pd.DataFrame, today_pool: pd.DataFrame) -> dict:
    """计算晋级率 = 昨日涨停今日继续涨停 / 昨日涨停总数。

    返回:
      {
        "yesterday_total": int,
        "promoted": int,
        "promotion_rate": float,
        "sector_promotion": {sector: rate},  # 板块维度晋级率
        "top_promoting_sectors": [...]
      }
    """
    out = {"yesterday_total": 0, "promoted": 0, "promotion_rate": 0.0,
           "sector_promotion": {}, "top_promoting_sectors": []}
    if yesterday is None or yesterday.empty:
        return out
    y = yesterday.rename(columns={"代码": "code", "所属行业": "sector"}).copy()
    if "code" not in y.columns:
        return out
    y["code"] = y["code"].astype(str).str.zfill(6)
    if "sector" not in y.columns:
        y["sector"] = ""
    yesterday_codes = set(y["code"])
    out["yesterday_total"] = len(yesterday_codes)

    if today_pool is None or today_pool.empty:
        return out
    t = today_pool.copy()
    if "symbol" in t.columns:
        t["code"] = t["symbol"].astype(str).str.split(".", n=1).str[0].str.zfill(6)
    elif "code" in t.columns:
        t["code"] = t["code"].astype(str).str.zfill(6)
    else:
        return out
    today_codes = set(t["code"])
    promoted_codes = yesterday_codes & today_codes
    out["promoted"] = len(promoted_codes)
    out["promotion_rate"] = round(len(promoted_codes) / max(len(yesterday_codes), 1), 4)

    # 板块维度:昨日某板块涨停 N 只,今日继续涨停 M 只
    if "sector" in y.columns:
        per_sector_total = y.groupby("sector")["code"].apply(set)
        per_sector_promoted = {sector: len(codes & today_codes)
                               for sector, codes in per_sector_total.items()}
        per_sector_total_count = {sector: len(codes) for sector, codes in per_sector_total.items()}
        sector_rate = {}
        for sector, total in per_sector_total_count.items():
            if total >= 1 and sector and sector not in ("", "未分组"):
                sector_rate[sector] = round(per_sector_promoted[sector] / total, 4)
        out["sector_promotion"] = sector_rate
        out["top_promoting_sectors"] = sorted(
            sector_rate.items(), key=lambda x: x[1], reverse=True
        )[:10]
    return out


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
    # Sanity 1: 封单 <= 成交额(A股口径硬约束)
    bad_seal = df["seal_amount"] > df["amount"]
    if bad_seal.any():
        print(f"warn: clipped {int(bad_seal.sum())} rows where seal_amount > amount")
        df.loc[bad_seal, "seal_amount"] = df.loc[bad_seal, "amount"]
    # Sanity 2: 炸板>=5次烂板剔除
    open_drop = df["open_times"] >= 5
    if open_drop.any():
        print(f"warn: dropped {int(open_drop.sum())} rows with open_times >= 5")
        df = df[~open_drop].copy()
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
    # 顺便算晋级率写入 stockbot/data/promotion_stats.json
    if not result.empty:
        try:
            import json as _json
            yest = fetch_yesterday_zt(args.date)
            stats = compute_promotion_stats(yest, result)
            stats["date"] = args.date
            stats_path = project_path("stockbot/data/promotion_stats.json")
            stats_path.parent.mkdir(parents=True, exist_ok=True)
            stats_path.write_text(_json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"promotion_rate={stats['promotion_rate']} yesterday_total={stats['yesterday_total']} promoted={stats['promoted']}")
            if stats.get("top_promoting_sectors"):
                for sector, rate in stats["top_promoting_sectors"][:5]:
                    print(f"  sector_promo {sector}={rate}")
        except Exception as exc:
            print(f"warn: promotion_stats failed: {exc}")
    if result.empty:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
