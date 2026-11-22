#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --locked --extra study --no-dev
uv run --locked --extra study --no-dev python -m compileall -q src/pypibt/crowd
uv run --locked --extra study --no-dev pytest -q
uv run --locked --extra study --no-dev python scripts/verify_results.py
