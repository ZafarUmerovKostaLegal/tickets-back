#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=tests
export E2E_FULL=1
pytest tests/e2e -m e2e_full -n auto "$@"
