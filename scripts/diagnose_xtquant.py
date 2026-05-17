"""Diagnose xtquant availability for the current Python environment."""
from __future__ import annotations

import importlib.util
import platform
import sys
from pathlib import Path


def main() -> None:
    print(f"python={sys.executable}")
    print(f"version={sys.version.split()[0]}")
    print(f"platform={platform.platform()}")
    spec = importlib.util.find_spec("xtquant")
    if spec is None:
        print("xtquant=NOT_FOUND")
        print("next=请把 QMT/miniQMT 提供的 xtquant 包安装到当前环境，或设置 PYTHONPATH 指向包含 xtquant 的目录")
        return
    print(f"xtquant=FOUND origin={spec.origin}")
    try:
        from xtquant import xtdata
        print("xtdata=OK")
        print(f"xtdata_file={Path(xtdata.__file__).resolve() if hasattr(xtdata, '__file__') else 'unknown'}")
    except Exception as exc:
        print(f"xtdata=ERROR {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
