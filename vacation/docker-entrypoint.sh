#!/bin/sh
set -e
MEDIA_DIR="${MEDIA_PATH:-/app/media}"
mkdir -p "$MEDIA_DIR/vacation_leave_requests"

# Named volumes are often root-owned; app runs as uid 10001 and must write PDFs here.
if [ "$(id -u)" = "0" ]; then
  chown -R 10001:0 "$MEDIA_DIR" 2>/dev/null || true
  chmod -R u+rwX,g+rwX "$MEDIA_DIR" 2>/dev/null || true
  if command -v runuser >/dev/null 2>&1; then
    exec runuser -u appuser -- "$@"
  fi
  if command -v setpriv >/dev/null 2>&1; then
    exec setpriv --reuid=10001 --regid=0 --clear-groups -- "$@"
  fi
  echo "vacation entrypoint: no runuser/setpriv; refusing to stay root" >&2
  exit 1
fi
exec "$@"
