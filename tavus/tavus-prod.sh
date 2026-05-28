#!/usr/bin/env bash
# Run the `tavus` CLI against PRODUCTION, overriding tavus-mcp/.env's localhost
# config and ignoring any stale TAVUS_API_KEY exported in your shell (so the
# OS-keychain key from `auth login` is used instead).
#
# Usage:
#   tavus/tavus-prod.sh auth login         # one-time: mint a prod key
#   tavus/tavus-prod.sh persona list       # verify auth works
#   tavus/tavus-prod.sh tool create --file tavus/advance_stove.tool.json
#
# Override any endpoint by exporting it before calling, e.g.:
#   TAVUS_DEV_PORTAL_URL=https://platform.tavus.io tavus/tavus-prod.sh auth login
set -euo pipefail
exec env -u TAVUS_API_KEY \
  TAVUS_ENV=PROD \
  TAVUS_PUBLIC_API_BASE_URL="${TAVUS_PUBLIC_API_BASE_URL:-https://tavusapi.com/v2}" \
  TAVUS_PORTAL_API_BASE_URL="${TAVUS_PORTAL_API_BASE_URL:-https://prod-api.tavus.io/api}" \
  TAVUS_DEV_PORTAL_URL="${TAVUS_DEV_PORTAL_URL:-https://persona-builder.tavus-preview.io}" \
  uv run --directory /Users/sclay/projects/tavus-mcp tavus "$@"
