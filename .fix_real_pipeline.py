from pathlib import Path
root = Path('/Users/bytedance/PycharmProjects/stock')

# akshare_market.py: cache, atomic-ish lower load, longer retry, turnover fallback friendly columns.
p = root / 'stockbot/adapters/akshare_market.py'
p.write_text('''"""AkShare market data adapter for macOS-friendly A-share data.

AkShare is used only for market data and screening. It is not a broker and
cannot place live orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import time

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_ROOT = Path.home() / ".cache" / "stockbot"


@dataclass(frozen=True)
class AkshareHistoryRequest:
    symbols: list[str]
    period: str = "daily"
    count: int = 120
    adjust: str = "qfq"


class AkshareMarketData:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        cache_cfg = cfg.get("market_data", {}).get("cache", {})
        self.cache_enabled = bool(cache_cfg.get("enabled", True))
        self.cache_root = Path(cache_cfg.get("root", CACHE_ROOT / "history")).expanduser()
        self.retries = int(cfg.get("market_data", {}).get("retries", 5))
        self.retry_sleep = float(cfg.get("market_data", {}).get("retry_sleep", 1.2))

    @staticmethod
    def strip_exchange(symbol: str) -> str:
        symbol = str(symbol).strip().upper()
        if symbol.startswith(("SH", "SZ", "BJ")):
            symbol = symbol[2:]
        return symbol.split(".", 1)[0].zfill(6)

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        code = AkshareMarketData.strip_exchange(symbol)
        if code.startswith(("6", "9")):
            return f"{code}.SH"
        return f"{code}.SZ"

    def get_history(self, request: AkshareHistoryRequest) -> dict[str, object]:
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError("未安装 akshare，请执行：.venv/bin/pip install akshare") from exc

        result: dict[str, object] = {}
        for raw_symbol in request.symbols:
            code = self.strip_exchange(raw_symbol)
            symbol = self.normalize_symbol(raw_symbol)
            cached = self._read_history_cache(symbol)
            need_fetch = cached is None or len(cached) < max(80, request.count)
            raw = cached
            last_error: Exception | None = None
            if need_fetch:
                for attempt in range(self.retries):
                    try:
                        raw = ak.stock_zh_a_hist(symbol=code, period=request.period, adjust=request.adjust)
                        self._write_history_cache(symbol, raw)
                        break
                    except Exception as exc:
                        last_error = exc
                        time.sleep(self.retry_sleep * (attempt + 1))
                if raw is None:
                    print(f"warn: skip {raw_symbol}, akshare error: {last_error}")
                    continue
            if raw is None or len(raw) == 0:
                continue
            df = raw.tail(request.count) if request.count > 0 else raw
            result[symbol] = self._normalize_frame(df, symbol)
        return result

    def _cache_path(self, symbol: str) -> Path:
        return self.cache_root / f"{symbol}.csv"

    def _read_history_cache(self, symbol: str):
        if not self.cache_enabled:
            return None
        path = self._cache_path(symbol)
        if not path.exists():
            return None
        try:
            return pd.read_csv(path)
        except Exception:
            return None

    def _write_history_cache(self, symbol: str, df: object) -> None:
        if not self.cache_enabled:
            return
        path = self._cache_path(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        df.to_csv(tmp, index=False, encoding="utf-8-sig")
        tmp.replace(path)

    def get_spot(self) -> object:
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError("未安装 akshare，请执行：.venv/bin/pip install akshare") from exc
        errors: list[Exception] = []
        for func_name in ("stock_zh_a_spot_em", "stock_zh_a_spot"):
            for attempt in range(max(2, self.retries)):
                try:
                    raw = getattr(ak, func_name)()
                    return self._normalize_spot_columns(raw)
                except Exception as exc:
                    errors.append(exc)
                    time.sleep(self.retry_sleep * (attempt + 1))
        raise RuntimeError(f"AkShare spot 接口均失败: {errors[-1] if errors else 'unknown'}")

    @staticmethod
    def _normalize_spot_columns(df: object) -> object:
        data = df.copy()
        rename = {
            "symbol": "代码", "code": "代码", "name": "名称", "trade": "最新价", "price": "最新价",
            "changepercent": "涨跌幅", "volume": "成交量", "amount": "成交额", "turnoverratio": "换手率",
            "mktcap": "总市值", "nmc": "流通市值",
        }
        for src, dst in rename.items():
            if src in data.columns and dst not in data.columns:
                data = data.rename(columns={src: dst})
        required = ["代码", "名称", "最新价", "涨跌幅", "成交量", "成交额", "换手率", "流通市值"]
        for col in required:
            if col not in data.columns:
                data[col] = 0 if col not in {"代码", "名称"} else ""
        return data

    def get_concept_boards(self) -> object:
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError("未安装 akshare，请执行：.venv/bin/pip install akshare") from exc
        return ak.stock_board_concept_name_em()

    def get_industry_boards(self) -> object:
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError("未安装 akshare，请执行：.venv/bin/pip install akshare") from exc
        return ak.stock_board_industry_name_em()

    def get_latest_quotes(self, symbols: Iterable[str]) -> dict[str, dict]:
        spot = self.get_spot()
        wanted = {self.strip_exchange(s) for s in symbols}
        rows = spot[spot["代码"].astype(str).str.lower().str.replace(r"^(sh|sz|bj)", "", regex=True).str.zfill(6).isin(wanted)]
        quotes: dict[str, dict] = {}
        for _, row in rows.iterrows():
            symbol = self.normalize_symbol(str(row["代码"]))
            quotes[symbol] = row.to_dict()
        return quotes

    @staticmethod
    def _normalize_frame(df: object, symbol: str) -> object:
        data = df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount", "振幅": "amplitude", "涨跌幅": "pct_change", "涨跌额": "change", "换手率": "turnover"}).copy()
        data["symbol"] = symbol
        data["name"] = data.get("name", "")
        data["sector"] = data.get("sector", "")
        data["industry"] = data.get("industry", "")
        for col in ["open", "close", "high", "low", "volume", "amount", "pct_change"]:
            if col in data.columns:
                data[col] = pd.to_numeric(data[col], errors="coerce").fillna(0.0)
            else:
                data[col] = 0.0
        data["ma20"] = data["close"].rolling(20).mean()
        data["ma60"] = data["close"].rolling(60).mean()
        data["volume_ma20"] = data["volume"].rolling(20).mean()
        return data.dropna(subset=["ma20", "ma60", "volume_ma20"])
''', encoding='utf-8')

# universe.py: clean output schema, turnover fallback, no 12% conflict mention handled via leader wrapper but universe scoring fixed.
p = root / 'stockbot/core/universe.py'
text = p.read_text(encoding='utf-8')
text = text.replace('''        for col in ["price", "pct_change", "volume", "amount", "turnover", "market_cap", "float_market_cap"]:\n            if col in df.columns:\n                df[col] = df[col].astype(float)\n            else:\n                df[col] = 0.0\n        return df\n''', '''        keep = ["code", "symbol", "name", "price", "pct_change", "volume", "amount", "turnover", "market_cap", "float_market_cap"]\n        for col in ["price", "pct_change", "volume", "amount", "turnover", "market_cap", "float_market_cap"]:\n            if col in df.columns:\n                df[col] = df[col].astype(float)\n            else:\n                df[col] = 0.0\n        if float(df["turnover"].abs().sum()) == 0.0:\n            base = df["float_market_cap"].where(df["float_market_cap"] > 0, df["market_cap"])\n            df["turnover"] = (df["amount"] / base.replace(0, float("nan")) * 100).fillna(0.0)\n        return df[[c for c in keep if c in df.columns]].copy()\n''')
text = text.replace('''        turnover_rank = df["turnover"].rank(pct=True)\n        momentum = df["pct_change"].clip(lower=-5, upper=10) / 10.0\n''', '''        turnover_rank = df["turnover"].rank(pct=True)\n        momentum = df["pct_change"].clip(lower=-5, upper=19.5) / 19.5\n''')
p.write_text(text, encoding='utf-8')

# dragon_head.py: unknown blocks buy.
p = root / 'stockbot/strategies/dragon_head.py'
text = p.read_text(encoding='utf-8')
text = text.replace('''        if phase in set(self.cfg.get("market_context", {}).get("block_buy_phases", ["panic", "cooldown"])):\n            return []\n''', '''        block_phases = set(self.cfg.get("market_context", {}).get("block_buy_phases", ["panic", "cooldown", "unknown"]))\n        if phase in block_phases:\n            return []\n''')
p.write_text(text, encoding='utf-8')

# daily_run.py: context failure early stop; allow fallback returns 0 but unknown blocks trader.
p = root / 'scripts/daily_run.py'
text = p.read_text(encoding='utf-8')
text = text.replace('''    market_context_step["optional"] = True\n    steps.append(market_context_step)\n\n    steps.append(\n''', '''    steps.append(market_context_step)\n    if market_context_step["returncode"] != 0:\n        log_path, summary_path = write_log(run_id, steps)\n        print(f"daily_run=FAILED step=build_market_context log={log_path} summary={summary_path}")\n        return market_context_step["returncode"]\n\n    steps.append(\n''')
text = text.replace('''        "ok": all(step["returncode"] == 0 or step.get("optional") for step in steps),''', '''        "ok": all(step["returncode"] == 0 for step in steps),''')
text = text.replace('''    ok = all(step["returncode"] == 0 or step.get("optional") for step in steps)\n''', '''    ok = all(step["returncode"] == 0 for step in steps)\n''')
p.write_text(text, encoding='utf-8')

# paper.py: atomic save + lock to reduce concurrent overwrite on mac/linux.
p = root / 'stockbot/adapters/paper.py'
text = p.read_text(encoding='utf-8')
text = text.replace('import json\nfrom pathlib import Path\n', 'import json\nimport os\nfrom pathlib import Path\n')
text = text.replace('''    def _save_state(self) -> None:\n        self.state_path.parent.mkdir(parents=True, exist_ok=True)\n        self.state_path.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")\n''', '''    def _save_state(self) -> None:\n        self.state_path.parent.mkdir(parents=True, exist_ok=True)\n        tmp = self.state_path.with_suffix(self.state_path.suffix + f".{os.getpid()}.tmp")\n        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")\n        tmp.replace(self.state_path)\n''')
# add reload before place_order to reduce stale process overwrite
text = text.replace('''    def place_order(self, order: OrderIntent) -> int:\n        if order.side == Side.BUY:\n''', '''    def place_order(self, order: OrderIntent) -> int:\n        self._state = self._load_state()\n        self._migrate_state()\n        if order.side == Side.BUY:\n''')
p.write_text(text, encoding='utf-8')

# install_daily_launchd.py default to 14:30.
p = root / 'scripts/install_daily_launchd.py'
text = p.read_text(encoding='utf-8').replace('parser.add_argument("--hour", type=int, default=9)', 'parser.add_argument("--hour", type=int, default=14)').replace('parser.add_argument("--minute", type=int, default=35)', 'parser.add_argument("--minute", type=int, default=30)')
p.write_text(text, encoding='utf-8')

# config: unknown blocks buy + market data cache config.
p = root / 'config.yaml'
text = p.read_text(encoding='utf-8')
text = text.replace('''market_data:\n  provider: akshare   # akshare | xtquant | tushare\n''', '''market_data:\n  provider: akshare   # akshare | xtquant | tushare\n  retries: 5\n  retry_sleep: 1.2\n  cache:\n    enabled: true\n    root: ~/.cache/stockbot/history\n''')
text = text.replace('''  block_buy_phases: ["panic", "cooldown"]\n''', '''  block_buy_phases: ["panic", "cooldown", "unknown"]\n''')
p.write_text(text, encoding='utf-8')

print('real_pipeline_fixes_done')
