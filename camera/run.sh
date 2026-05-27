#!/usr/bin/env bash
# Launch the Stove Guy virtual presentation camera server (localhost only).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating venv + installing deps..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt
fi

echo "Stove Guy camera control:  http://127.0.0.1:8000"
# --timeout-graceful-shutdown: the live MJPEG preview + WebSocket hold
# connections open forever, so cap graceful shutdown to a few seconds. This
# lets the camera close cleanly on Ctrl-C / restart (a stuck shutdown can
# leave the OBS device feeding a stale frame to attached apps like Photo Booth).
exec .venv/bin/python -m uvicorn app.server:app \
  --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 3 "$@"
