#!/bin/sh
set -e
LV=$(printf '%s' "${LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')
[ -z "$LV" ] && LV=info
exec uvicorn main:app --host 0.0.0.0 --port 1242 --log-level "$LV"
