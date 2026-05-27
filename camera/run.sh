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
exec .venv/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port 8000 "$@"
