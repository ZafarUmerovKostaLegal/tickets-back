# Attendance camera events — persistence & ingest
#
# What was added
# - Table `attendance_camera_events` stores raw Hikvision AcsEvent rows
# - Continuous poller (default every 5 min, lookback 3h) upserts fresh events
# - Backfill CLI / API to pull a full date range (e.g. all of 2026) into Postgres
#
# Note: cameras often keep only a limited on-device log. If device retention is
# shorter than a year, early-2026 events may already be gone from the camera —
# only what the device still returns can be ingested.
#
# Continuous ingest (enabled by default)
#   ATTENDANCE_INGEST_ENABLED=true
#   ATTENDANCE_INGEST_INTERVAL_SEC=300
#   ATTENDANCE_INGEST_LOOKBACK_MINUTES=180
#
# One-shot backfill for 2026 (pick one):
#
# 1) CLI inside the attendance container:
#    docker compose exec attendance python -m application.cli backfill --year 2026
#    docker compose exec attendance python -m application.cli status
#
# 2) HTTP (via gateway, auth required):
#    POST /api/v1/attendance/hikvision/ingest/backfill?year=2026
#    GET  /api/v1/attendance/hikvision/ingest/status
#
# 3) Auto-start on boot (optional):
#    ATTENDANCE_BACKFILL_FROM=2026-01-01
#    ATTENDANCE_BACKFILL_TO=2026-12-31
#
