"""A-share universe filtering and ranking."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UniverseResult:
    symbols: list[str]
    rows: object


class AShareUniverseFilter:
    """规则化股票池过滤：先排风险，再按积极成长偏好排序。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.rules = cfg.get("universe_filter", {})

    @staticmethod
    def normalize_symbol(code: str) -> str:
        code = str(code).strip().lower()
        if code.startswith(("sh", "sz", "bj")):
            code = code[2:]
        code = code.zfill(6)
        if code.startswith(("6", "9")):
            return f"{code}.SH"
        return f"{code}.SZ"

    def filter_spot(self, spot: object, limit: int | None = None) -> UniverseResult:
        df = self._normalize_spot(spot)
        df = self._apply_hard_filters(df)
        df = self._score(df)
        df = df.sort_values(["score", "amount"], ascending=[False, False])
        if limit:
            df = df.head(limit)
        return UniverseResult(symbols=df["symbol"].tolist(), rows=df)

    def save(self, result: UniverseResult, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        result.rows.to_csv(path, index=False, encoding="utf-8-sig")

    def _normalize_spot(self, spot: object) -> object:
        df = spot.rename(
            columns={
                "代码": "code",
                "名称": "name",
                "最新价": "price",
                "涨跌幅": "pct_change",
                "成交量": "volume",
                "成交额": "amount",
                "换手率": "turnover",
                "总市值": "market_cap",
                "流通市值": "float_market_cap",
            }
        ).copy()
        df["code"] = df["code"].astype(str).str.lower().str.replace(r"^(sh|sz|bj)", "", regex=True).str.zfill(6)
        df["symbol"] = df["code"].map(self.normalize_symbol)
        df["name"] = df["name"].astype(str)
        keep = ["code", "symbol", "name", "price", "pct_change", "volume", "amount", "turnover", "market_cap", "float_market_cap"]
        for col in ["price", "pct_change", "volume", "amount", "turnover", "market_cap", "float_market_cap"]:
            if col in df.columns:
                df[col] = df[col].astype(float)
            else:
                df[col] = 0.0
        if float(df["turnover"].abs().sum()) == 0.0:
            base = df["float_market_cap"].where(df["float_market_cap"] > 0, df["market_cap"])
            df["turnover"] = (df["amount"] / base.replace(0, float("nan")) * 100).fillna(0.0)
        return df[[c for c in keep if c in df.columns]].copy()

    def _apply_hard_filters(self, df: object) -> object:
        min_price = float(self.rules.get("min_price", 3.0))
        min_amount = float(self.rules.get("min_amount", 80_000_000))
        max_pct_change = float(self.rules.get("max_abs_pct_change", 19.5))
        exclude_bj = bool(self.rules.get("exclude_bj", True))
        excluded_name_keywords = self.rules.get("excluded_name_keywords", ["ST", "*ST", "退"])
        excluded_prefixes = tuple(str(x) for x in self.rules.get("excluded_code_prefixes", []))

        mask = df["price"] >= min_price
        mask &= df["amount"] >= min_amount
        mask &= df["pct_change"].abs() <= max_pct_change
        for keyword in excluded_name_keywords:
            mask &= ~df["name"].str.contains(str(keyword), regex=False)
        if excluded_prefixes:
            mask &= ~df["code"].str.startswith(excluded_prefixes)
        if exclude_bj:
            mask &= ~df["code"].str.startswith(("4", "8", "920"))
        return df[mask].copy()

    def _score(self, df: object) -> object:
        preferred_prefixes = tuple(str(x) for x in self.rules.get("preferred_code_prefixes", ["300", "301", "688", "002"]))
        main_prefixes = tuple(str(x) for x in self.rules.get("main_code_prefixes", ["000", "600", "601", "603", "605"] ))
        df = df.copy()
        amount_rank = df["amount"].rank(pct=True)
        turnover_rank = df["turnover"].rank(pct=True)
        momentum = df["pct_change"].clip(lower=-5, upper=19.5) / 19.5
        board_bonus = df["code"].str.startswith(preferred_prefixes).astype(float) * 0.25
        board_bonus += df["code"].str.startswith(main_prefixes).astype(float) * 0.05
        df["score"] = amount_rank * 0.45 + turnover_rank * 0.25 + momentum * 0.20 + board_bonus
        return df
