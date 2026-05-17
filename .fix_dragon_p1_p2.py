from pathlib import Path
root = Path('/Users/bytedance/PycharmProjects/stock')

# 1) engine.signal_to_order: skip BUY when already holding same symbol.
p = root / 'stockbot/core/engine.py'
text = p.read_text(encoding='utf-8')
old = '''    def signal_to_order(self, account: AccountSnapshot, signal: Signal) -> OrderIntent | None:\n        if signal.price is None or signal.price <= 0:\n            return None\n        if signal.side == Side.SELL:\n'''
new = '''    def signal_to_order(self, account: AccountSnapshot, signal: Signal) -> OrderIntent | None:\n        if signal.price is None or signal.price <= 0:\n            return None\n        if signal.side == Side.BUY and signal.symbol in account.positions:\n            return None\n        if signal.side == Side.SELL:\n'''
if old not in text:
    raise SystemExit('engine signal_to_order needle not found')
p.write_text(text.replace(old, new), encoding='utf-8')

# 2) dragon_head: high>low guard, fail-fast corrupt context, no name fallback, blank sector protection.
p = root / 'stockbot/strategies/dragon_head.py'
text = p.read_text(encoding='utf-8')
old = '''        near_high = recent_high > 0 and close >= recent_high * (1 - self.params.near_high_pct)\n        strong_body = open_ > 0 and close > open_ and (close - open_) / open_ >= 0.012\n        close_near_high = high > low and (high - close) / max(high - low, 0.01) <= 0.25\n\n        # 涨停/准涨停不是过滤对象；龙头战法的核心恰恰来自涨停梯队。\n'''
new = '''        if high <= low:\n            return None\n        near_high = recent_high > 0 and close >= recent_high * (1 - self.params.near_high_pct)\n        strong_body = open_ > 0 and close > open_ and (close - open_) / open_ >= 0.012\n        close_near_high = (high - close) / max(high - low, 0.01) <= 0.25\n\n        # 涨停/准涨停不是过滤对象；龙头战法的核心恰恰来自涨停梯队。\n'''
if old not in text:
    raise SystemExit('buy_point needle not found')
text = text.replace(old, new)
old = '''        if "sector" not in df.columns:\n            df["sector"] = ""\n        if "first_limit_time" not in df.columns:\n            df["first_limit_time"] = 999999\n        df["first_limit_num"] = pd.to_numeric(df["first_limit_time"], errors="coerce").fillna(999999)\n        df["first_limit_rank"] = df.groupby("sector")["first_limit_num"].rank(method="min", pct=True)\n        df["sector_rank_score"] = (\n            df["limit_up_count"] * 1000000\n            + df["seal_amount"].rank(pct=True) * 1000\n            + df["amount"].rank(pct=True) * 100\n            - df["open_times"] * 10\n            - df["first_limit_rank"]\n        )\n        df["sector_rank"] = df.groupby("sector")["sector_rank_score"].rank(method="first", ascending=False).astype(int)\n        df["sector_max_limit"] = df.groupby("sector")["limit_up_count"].transform("max")\n        df["role"] = "back_row"\n        df.loc[(df["sector_rank"] == 1) & (df["limit_up_count"] >= df["sector_max_limit"]), "role"] = "absolute_leader"\n        df.loc[(df["sector_rank"] == 1) & (df["limit_up_count"] < df["sector_max_limit"]), "role"] = "position_switch"\n        return df\n'''
new = '''        if "sector" not in df.columns:\n            df["sector"] = ""\n        df["sector"] = df["sector"].astype(str).str.strip()\n        if "first_limit_time" not in df.columns:\n            df["first_limit_time"] = 999999\n        df["role"] = "unknown"\n        df["sector_rank"] = 999\n        df["sector_max_limit"] = 0\n        invalid_sector = df["sector"].isin(["", "nan", "None", "未分组", "未知"])\n        valid = df[~invalid_sector].copy()\n        if valid.empty:\n            return df\n        valid["first_limit_num"] = pd.to_numeric(valid["first_limit_time"], errors="coerce").fillna(999999)\n        valid["first_limit_rank"] = valid.groupby("sector")["first_limit_num"].rank(method="min", pct=True)\n        valid["sector_rank_score"] = (\n            valid["limit_up_count"] * 1000000\n            + valid["seal_amount"].rank(pct=True) * 1000\n            + valid["amount"].rank(pct=True) * 100\n            - valid["open_times"] * 10\n            - valid["first_limit_rank"]\n        )\n        valid["sector_rank"] = valid.groupby("sector")["sector_rank_score"].rank(method="first", ascending=False).astype(int)\n        valid["sector_max_limit"] = valid.groupby("sector")["limit_up_count"].transform("max")\n        valid["role"] = "back_row"\n        valid.loc[(valid["sector_rank"] == 1) & (valid["limit_up_count"] >= valid["sector_max_limit"]), "role"] = "absolute_leader"\n        valid.loc[(valid["sector_rank"] == 1) & (valid["limit_up_count"] < valid["sector_max_limit"]), "role"] = "position_switch"\n        for col in ["first_limit_num", "first_limit_rank", "sector_rank_score", "sector_rank", "sector_max_limit", "role"]:\n            df.loc[valid.index, col] = valid[col]\n        return df\n'''
if old not in text:
    raise SystemExit('sector_rank needle not found')
text = text.replace(old, new)
old = '''    def _pool_row(self, symbol: str, name: str) -> dict | None:\n        if self.dragon_pool.empty:\n            return None\n        rows = self.dragon_pool[self.dragon_pool["symbol"].astype(str) == symbol]\n        if rows.empty and name:\n            rows = self.dragon_pool[self.dragon_pool["name"].astype(str) == name]\n        if rows.empty:\n            return None\n        return rows.iloc[0].to_dict()\n'''
new = '''    def _pool_row(self, symbol: str, name: str) -> dict | None:\n        if self.dragon_pool.empty:\n            return None\n        rows = self.dragon_pool[self.dragon_pool["symbol"].astype(str) == symbol]\n        if rows.empty:\n            return None\n        return rows.iloc[0].to_dict()\n'''
if old not in text:
    raise SystemExit('pool_row needle not found')
text = text.replace(old, new)
old = '''    @staticmethod\n    def _load_json(path: str) -> dict:\n        p = _project_path(path)\n        if not p.exists():\n            return {}\n        try:\n            return json.loads(p.read_text(encoding="utf-8"))\n        except Exception:\n            return {}\n'''
new = '''    @staticmethod\n    def _load_json(path: str) -> dict:\n        p = _project_path(path)\n        if not p.exists():\n            return {}\n        try:\n            return json.loads(p.read_text(encoding="utf-8"))\n        except json.JSONDecodeError as exc:\n            raise RuntimeError(f"market_context JSON 损坏，请重建: {p}") from exc\n'''
if old not in text:
    raise SystemExit('load_json needle not found')
text = text.replace(old, new)
p.write_text(text, encoding='utf-8')

# 3) config: tail-session dragon trade window.
p = root / 'config.yaml'
text = p.read_text(encoding='utf-8')
text = text.replace('  block_new_buy_after: "14:30:00"', '  block_new_buy_after: "14:57:00"')
p.write_text(text, encoding='utf-8')

print('fixed_dragon_p1_p2')
