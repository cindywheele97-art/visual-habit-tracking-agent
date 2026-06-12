# Glimpse

A persistent macOS screen agent. v1: watch a chat window region, OCR new
customer messages locally, show grounded reply suggestions in an overlay.
Spec: `docs/superpowers/specs/2026-06-11-glimpse-v1-design.md`.

## Setup

```bash
# Brain (Python 3.11+)
cd brain && python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Shell (Swift 5.9+, macOS 13+)
cd ../shell && swift build

# Your playbook (edit it — suggestions are grounded in this file)
mkdir -p ~/.glimpse && cp ../playbook/playbook.md ~/.glimpse/playbook.md
```

## Run

```bash
export ANTHROPIC_API_KEY=sk-ant-...
./scripts/dev.sh
```

First run: macOS asks for Screen Recording permission for your terminal app
(System Settings → Privacy & Security → Screen Recording). Grant it and
restart `dev.sh`.

Menu bar 👁 → "Select Region & Watch" → drag over the chat window. Suggestions
appear in the Glimpse panel; click 复制 and paste into your chat app.

## Privacy

- Screenshots never leave the shell process and never touch disk.
- Only regex-redacted text (config `[redaction]`) is sent to the LLM.
- The event log (`~/.glimpse/events.jsonl`) stores redacted text only.

## Tests

```bash
cd brain && pytest && ruff check src tests && mypy
cd shell && swift test
```

## E2E smoke (spec §8 gate, run by a human)

1. `./scripts/dev.sh`, open `harness/fake_chat.html` in a browser.
2. Select the chat region; click "下一条客户消息"; expect suggestions ≤5 s.
3. Wait 30 s: no duplicate suggestions. Send a reply via 发送: no trigger.
4. 复制 a suggestion → `~/.glimpse/events.jsonl` gains `suggestion_copied`.
5. Idle 10 min: shell CPU ~0–3% in Activity Monitor; no images under `~/.glimpse/`.
