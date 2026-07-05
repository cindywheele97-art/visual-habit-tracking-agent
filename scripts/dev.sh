#!/usr/bin/env bash
# Launch brain + shell together for development. Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ANTHROPIC_API_KEY is not set" >&2
  exit 1
fi

# Single config source of truth: ~/.glimpse/glimpse.toml — the file the README
# documents and the evals runner loads. Seed it from the repo default on first
# run; never overwrite user edits.
CONFIG="$HOME/.glimpse/glimpse.toml"
if [[ ! -f "$CONFIG" ]]; then
  mkdir -p "$HOME/.glimpse"
  cp config/glimpse.toml "$CONFIG"
  echo "seeded $CONFIG from config/glimpse.toml"
fi
if [[ ! -d "$HOME/.glimpse/knowledge" ]]; then
  cp -R playbook/knowledge "$HOME/.glimpse/knowledge"
  echo "seeded $HOME/.glimpse/knowledge from playbook/knowledge"
fi
if [[ ! -f "$HOME/.glimpse/playbook.md" ]]; then
  cp playbook/playbook.md "$HOME/.glimpse/playbook.md"
  echo "seeded $HOME/.glimpse/playbook.md from playbook/playbook.md"
fi

(cd brain && source .venv/bin/activate && exec python -m glimpse_brain --config "$CONFIG") &
BRAIN_PID=$!
trap 'kill "$BRAIN_PID" 2>/dev/null || true' EXIT INT TERM

(cd shell && swift run -c release GlimpseShell)
