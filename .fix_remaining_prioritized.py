from pathlib import Path
root = Path('/Users/bytedance/PycharmProjects/stock')

# build_market_context: preserve previous successful context when fallback would write unknown.
p = root / 'scripts/build_market_context.py'
text = p.read_text(encoding='utf-8')
text = text.replace('''import argparse\nimport multiprocessing as mp\nimport sys\nimport time\nfrom datetime import datetime\nfrom pathlib import Path\n''', '''import argparse\nimport json\nimport multiprocessing as mp\nimport sys\nimport time\nfrom datetime import datetime\nfrom pathlib import Path\n''')
old = '''def _write_unknown_context(output: str, reason: str) -> Path:\n    heat = SectorHeatAnalyzer({})\n    emotion = EmotionSnapshot("unknown", 0.0, 0, 0, 0, 0, 0, 0, 0, 0.0)\n    return heat.save_context(emotion, [], [], str(_project_path(output)))\n'''
new = '''def _has_valid_existing_context(path: Path) -> bool:\n    if not path.exists():\n        return False\n    try:\n        payload = json.loads(path.read_text(encoding="utf-8"))\n    except Exception:\n        return False\n    emotion = payload.get("emotion", {}) or {}\n    return emotion.get("phase") not in {None, "", "unknown"} and int(emotion.get("total", 0) or 0) > 0\n\n\ndef _write_unknown_context(output: str, reason: str) -> Path:\n    path = _project_path(output)\n    if _has_valid_existing_context(path):\n        print(f"warn: keep_existing_market_context=true reason={reason} path={path}")\n        return path\n    heat = SectorHeatAnalyzer({})\n    emotion = EmotionSnapshot("unknown", 0.0, 0, 0, 0, 0, 0, 0, 0, 0.0)\n    return heat.save_context(emotion, [], [], str(path))\n'''
if old not in text:
    raise SystemExit('build_market_context unknown writer needle not found')
text = text.replace(old, new)
old = '''        emotion = EmotionSnapshot("unknown", 0.0, 0, 0, 0, 0, 0, 0, 0, 0.0)\n        path = heat.save_context(emotion, [], [], str(output_path))\n        print(f"market_context={path}")\n        print("emotion phase=unknown score=0.0 fallback=true")\n        return\n'''
new = '''        path = _write_unknown_context(str(output_path), "spot_unavailable")\n        print(f"market_context={path}")\n        if _has_valid_existing_context(path):\n            print("fallback=true action=keep_existing_context")\n        else:\n            print("emotion phase=unknown score=0.0 fallback=true")\n        return\n'''
if old not in text:
    raise SystemExit('build_market_context spot fallback needle not found')
text = text.replace(old, new)
p.write_text(text, encoding='utf-8')

# dragon_head: add paper/live cannot-buy guard; stricter max_open_times config handled below.
p = root / 'stockbot/strategies/dragon_head.py'
text = p.read_text(encoding='utf-8')
old = '''        buy_point = self._buy_point(close, open_, high, low, recent_high, pct_change, open_times)\n        if not buy_point:\n            return None\n'''
new = '''        if self._cannot_buy_live(symbol, open_, high, low, pct_change):\n            return None\n        buy_point = self._buy_point(close, open_, high, low, recent_high, pct_change, open_times)\n        if not buy_point:\n            return None\n'''
if old not in text:
    raise SystemExit('dragon live cannot-buy insertion needle not found')
text = text.replace(old, new)
insert = '''\n    @staticmethod\n    def _limit_up_threshold(symbol: str) -> float:\n        code = str(symbol).split(".", 1)[0]\n        return 19.5 if code.startswith(("300", "301", "688")) else 9.5\n\n    @classmethod\n    def _cannot_buy_live(cls, symbol: str, open_: float, high: float, low: float, pct_change: float) -> bool:\n        # 一字板/极端涨停无法稳定成交；跌停和无效开盘价也不发 paper/live BUY。\n        limit_threshold = cls._limit_up_threshold(symbol)\n        return open_ <= 0 or pct_change <= -9.5 or (high <= low and pct_change >= limit_threshold)\n\n'''
text = text.replace('''    def _buy_point(self, close: float, open_: float, high: float, low: float, recent_high: float, pct_change: float, open_times: int) -> str | None:\n''', insert + '    def _buy_point(self, close: float, open_: float, high: float, low: float, recent_high: float, pct_change: float, open_times: int) -> str | None:\n')
# load_json: user asked warn+{} not raise.
old = '''        except json.JSONDecodeError as exc:\n            raise RuntimeError(f"market_context JSON 损坏，请重建: {p}") from exc\n'''
new = '''        except Exception as exc:\n            import sys\n            print(f"warn: load_json {p} failed: {exc}", file=sys.stderr)\n            return {}\n'''
if old in text:
    text = text.replace(old, new)
p.write_text(text, encoding='utf-8')

# backtest: 10cm/20cm threshold in point-in-time dragon pool and cannot_buy.
p = root / 'stockbot/core/backtest.py'
text = p.read_text(encoding='utf-8')
old = '''            if pct < 9.5 or close <= 0:\n                continue\n            streak = 0\n            for x in reversed(pd.to_numeric(df.get("pct_change", pd.Series(dtype=float)).tail(10), errors="coerce").fillna(0).tolist()):\n                if float(x) >= 9.5:\n                    streak += 1\n                else:\n                    break\n'''
new = '''            threshold = Backtester._limit_up_threshold(symbol)\n            if pct < threshold or close <= 0:\n                continue\n            streak = 0\n            for x in reversed(pd.to_numeric(df.get("pct_change", pd.Series(dtype=float)).tail(10), errors="coerce").fillna(0).tolist()):\n                if float(x) >= threshold:\n                    streak += 1\n                else:\n                    break\n'''
if old not in text:
    raise SystemExit('backtest threshold needle not found')
text = text.replace(old, new)
old = '''    @staticmethod\n    def _cannot_buy(row: pd.Series) -> bool:\n        pct = float(row.get("pct_change", 0) or 0)\n        open_ = float(row.get("open", 0) or 0)\n        high = float(row.get("high", 0) or 0)\n        low = float(row.get("low", 0) or 0)\n        # 一字/极端涨停无法按 open 稳定成交；跌停也不买。\n        return pct >= 19.8 or pct <= -9.5 or (high == low and pct >= 9.5) or open_ <= 0\n'''
new = '''    @staticmethod\n    def _limit_up_threshold(symbol: str) -> float:\n        code = str(symbol).split(".", 1)[0]\n        return 19.5 if code.startswith(("300", "301", "688")) else 9.5\n\n    @staticmethod\n    def _cannot_buy(row: pd.Series) -> bool:\n        pct = float(row.get("pct_change", 0) or 0)\n        open_ = float(row.get("open", 0) or 0)\n        high = float(row.get("high", 0) or 0)\n        low = float(row.get("low", 0) or 0)\n        symbol = str(row.get("symbol", ""))\n        threshold = Backtester._limit_up_threshold(symbol)\n        # 一字/极端涨停无法按 open 稳定成交；跌停也不买。\n        return pct >= 19.8 or pct <= -9.5 or (high <= low and pct >= threshold) or open_ <= 0\n'''
if old not in text:
    raise SystemExit('backtest cannot_buy needle not found')
text = text.replace(old, new)
p.write_text(text, encoding='utf-8')

# akshare_market: inject code->name map from spot when possible.
p = root / 'stockbot/adapters/akshare_market.py'
text = p.read_text(encoding='utf-8')
old = '''        result: dict[str, object] = {}\n        for raw_symbol in request.symbols:\n'''
new = '''        name_map = self._load_name_map(request.symbols)\n        result: dict[str, object] = {}\n        for raw_symbol in request.symbols:\n'''
if old not in text:
    raise SystemExit('ak get_history name_map needle not found')
text = text.replace(old, new)
old = '''            result[symbol] = self._normalize_frame(df, symbol)\n        return result\n\n    @staticmethod\n    def _cache_recent_enough'''
new = '''            result[symbol] = self._normalize_frame(df, symbol, name_map.get(symbol, ""))\n        return result\n\n    def _load_name_map(self, symbols: list[str]) -> dict[str, str]:\n        try:\n            spot = self.get_spot()\n            wanted = {self.strip_exchange(s) for s in symbols}\n            codes = spot["代码"].astype(str).str.lower().str.replace(r"^(sh|sz|bj)", "", regex=True).str.zfill(6)\n            rows = spot[codes.isin(wanted)].copy()\n            rows["_symbol"] = codes[codes.isin(wanted)].map(self.normalize_symbol)\n            return dict(zip(rows["_symbol"].astype(str), rows["名称"].astype(str)))\n        except Exception:\n            return {}\n\n    @staticmethod\n    def _cache_recent_enough'''
if old not in text:
    raise SystemExit('ak normalize_frame call needle not found')
text = text.replace(old, new)
text = text.replace('''    def _normalize_frame(df: object, symbol: str) -> object:\n''', '''    def _normalize_frame(df: object, symbol: str, name: str = "") -> object:\n''')
text = text.replace('''        data["symbol"] = symbol\n        data["name"] = data.get("name", "")\n''', '''        data["symbol"] = symbol\n        data["name"] = name or data.get("name", "")\n''')
p.write_text(text, encoding='utf-8')

# report.py: account JSON guard.
p = root / 'stockbot/core/report.py'
text = p.read_text(encoding='utf-8')
old = '''    def _read_account(self) -> dict[str, Any]:\n        if not self.paper_state.exists():\n            return {"cash": 0.0, "positions": {}, "orders": []}\n        return json.loads(self.paper_state.read_text(encoding="utf-8"))\n'''
new = '''    def _read_account(self) -> dict[str, Any]:\n        if not self.paper_state.exists():\n            return {"cash": 0.0, "positions": {}, "orders": []}\n        try:\n            return json.loads(self.paper_state.read_text(encoding="utf-8"))\n        except Exception:\n            return {"cash": 0.0, "positions": {}, "orders": [], "warning": "paper_account_json_invalid"}\n'''
if old not in text:
    raise SystemExit('report read_account needle not found')
p.write_text(text.replace(old, new), encoding='utf-8')

# config: block 14:55, stricter open times.
p = root / 'config.yaml'
text = p.read_text(encoding='utf-8')
text = text.replace('  block_new_buy_after: "14:57:00"', '  block_new_buy_after: "14:55:00"')
text = text.replace('  max_open_times: 3', '  max_open_times: 1')
p.write_text(text, encoding='utf-8')

print('remaining_prioritized_fixed')
