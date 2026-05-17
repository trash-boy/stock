#!/bin/bash
set -e
cd "$(dirname "$0")/.."
PY=.venv/bin/python
echo step1; PYTHONPATH=. $PY scripts/build_market_context.py --timeout 60
echo step2; PYTHONPATH=. $PY scripts/build_dragon_pool.py --allow-fallback
echo step3; PYTHONPATH=. $PY scripts/run_backtest.py --symbols 000001.SZ,600036.SH,000858.SZ --count 60 --output stockbot/backtests/smoke
cat stockbot/backtests/smoke/metrics.json
echo step4; $PY scripts/install_daily_launchd.py
launchctl list | grep stockbot || echo not_installed
echo DONE
