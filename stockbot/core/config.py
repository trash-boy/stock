"""Configuration loader."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def load_config(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError("缺少 PyYAML，请执行：pip install --user --break-system-packages pyyaml")
        return yaml.safe_load(text)
    return json.loads(text)
