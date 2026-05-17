"""Market emotion cycle and sector heat analysis."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EmotionSnapshot:
    phase: str
    score: float
    total: int
    up_count: int
    down_count: int
    limit_up_count: int
    limit_down_count: int
    strong_count: int
    weak_count: int
    median_pct_change: float


class EmotionCycleAnalyzer:
    """Classify A-share short-term emotion from market breadth.

    Phase meaning:
    - panic: 情绪冰点，禁止主动开仓
    - repair: 修复期，小仓试错
    - ferment: 发酵期，可参与主线龙头
    - climax: 高潮期，只做最强，禁止后排追高
    - cooldown: 退潮期，主动降仓
    """

    def analyze_spot(self, spot: object) -> EmotionSnapshot:
        df = self._normalize(spot)
        total = len(df)
        if total == 0:
            return EmotionSnapshot("unknown", 0.0, 0, 0, 0, 0, 0, 0, 0, 0.0)
        pct = df["pct_change"].astype(float)
        up_count = int((pct > 0).sum())
        down_count = int((pct < 0).sum())
        limit_up_count = int((pct >= 9.5).sum())
        limit_down_count = int((pct <= -9.5).sum())
        strong_count = int((pct >= 5).sum())
        weak_count = int((pct <= -5).sum())
        median_pct = float(pct.median())
        up_ratio = up_count / total
        limit_ratio = limit_up_count / max(limit_up_count + limit_down_count, 1)
        strong_ratio = strong_count / max(strong_count + weak_count, 1)
        score = up_ratio * 0.45 + limit_ratio * 0.35 + strong_ratio * 0.20
        phase = self._phase(score, limit_up_count, limit_down_count, median_pct)
        return EmotionSnapshot(
            phase=phase,
            score=round(score, 4),
            total=total,
            up_count=up_count,
            down_count=down_count,
            limit_up_count=limit_up_count,
            limit_down_count=limit_down_count,
            strong_count=strong_count,
            weak_count=weak_count,
            median_pct_change=round(median_pct, 4),
        )

    @staticmethod
    def _phase(score: float, limit_up: int, limit_down: int, median_pct: float) -> str:
        if score < 0.28 or (limit_down >= max(limit_up, 1) and median_pct < -1):
            return "panic"
        if score < 0.45:
            return "repair"
        if score < 0.68:
            return "ferment"
        if limit_up >= 80 and median_pct > 1.2:
            return "climax"
        return "cooldown" if median_pct < -0.3 and score < 0.55 else "ferment"

    @staticmethod
    def _normalize(spot: object) -> object:
        df = spot.rename(columns={"涨跌幅": "pct_change"}).copy()
        if "pct_change" not in df.columns:
            df["pct_change"] = 0.0
        return df


class SectorHeatAnalyzer:
    """Rank concept/industry boards by heat."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.keywords = [str(x) for x in cfg.get("sector_heat", {}).get("preferred_keywords", [])]

    def rank_boards(self, boards: object, top_n: int = 20) -> list[dict[str, Any]]:
        df = self._normalize(boards)
        if len(df) == 0:
            return []
        pct_rank = df["pct_change"].rank(pct=True)
        amount_rank = df["amount"].rank(pct=True)
        up_rank = df["up_count"].rank(pct=True)
        keyword_bonus = df["name"].map(lambda x: 0.25 if self._preferred(str(x)) else 0.0)
        df["heat_score"] = pct_rank * 0.45 + amount_rank * 0.30 + up_rank * 0.15 + keyword_bonus
        df = df.sort_values("heat_score", ascending=False).head(top_n)
        return [dict(row) for _, row in df.iterrows()]

    def save_context(self, emotion: EmotionSnapshot, concept_heat: list[dict], industry_heat: list[dict], output: str | Path) -> Path:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "emotion": emotion.__dict__,
            "concept_heat": concept_heat,
            "industry_heat": industry_heat,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return path

    def _preferred(self, name: str) -> bool:
        return any(key in name for key in self.keywords)

    @staticmethod
    def _normalize(boards: object) -> object:
        df = boards.rename(
            columns={
                "板块名称": "name",
                "涨跌幅": "pct_change",
                "成交额": "amount",
                "上涨家数": "up_count",
                "下跌家数": "down_count",
                "领涨股票": "leader_stock",
                "领涨股票-涨跌幅": "leader_pct_change",
            }
        ).copy()
        for col in ["name", "leader_stock"]:
            if col not in df.columns:
                df[col] = ""
        for col in ["pct_change", "amount", "up_count", "down_count", "leader_pct_change"]:
            if col not in df.columns:
                df[col] = 0.0
            df[col] = df[col].fillna(0).astype(float)
        return df[["name", "pct_change", "amount", "up_count", "down_count", "leader_stock", "leader_pct_change"]]
