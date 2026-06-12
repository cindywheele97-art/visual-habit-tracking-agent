#!/usr/bin/env bash
# Launch brain + shell together for development. Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ANTHROPIC_API_KEY is not set" >&2
  exit 1
fi

(cd brain && source .venv/bin/activate && exec python -m glimpse_brain --config ../config/glimpse.toml) &
BRAIN_PID=$!
trap 'kill "$BRAIN_PID" 2>/dev/null || true' EXIT INT TERM

(cd shell && swift run -c release GlimpseShell)
