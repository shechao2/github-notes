#!/usr/bin/env bash
# macOS / Linux 一键启动
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
[ -f data/app.db ] || python -m app.seed
python -m app.main
