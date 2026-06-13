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
# (run from shell/, i.e. right after the previous step)
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
cd ../shell && swift test
```

## E2E smoke (spec §8 gate, run by a human)

1. `./scripts/dev.sh`, open `harness/fake_chat.html` in a browser.
2. Select the chat region; click "下一条客户消息"; expect suggestions ≤5 s.
3. Wait 30 s: no duplicate suggestions. Send a reply via 发送: no trigger.
4. 复制 a suggestion → `~/.glimpse/events.jsonl` gains `suggestion_copied`.
5. Idle 10 min: shell CPU ~0–3% in Activity Monitor; no images under `~/.glimpse/`.

## Phase 2 — habit tracking (click capture + daily interest summary)

Glimpse can also record *what you click* in opted-in apps and summarize the day's
interests on demand. Capture only runs for apps you list — nothing else is read.

### Setup

```bash
# Opt in the apps to observe (bundle IDs). Start from the example:
cp config/allowlist.example.json ~/.glimpse/allowlist.json
# Edit to taste. Find a bundle id with:  osascript -e 'id of app "Google Chrome"'
```

First run also needs **Accessibility** permission for your terminal/app
(System Settings → Privacy & Security → Accessibility) so the click sensor can
install. Without it the overlay shows "Accessibility needed for click tracking";
the rest of Glimpse still works.

### Use

Click around in an allowlisted browser, then menu bar 👁 → **Today's Interests**.
A summary of what you looked at appears in the overlay.

### Privacy

- Clicks in non-allowlisted apps capture **nothing** — no pixels are read.
- Snapshots are bounded (~600×400 around the click), OCR'd locally, never persisted
  as images; only redacted text reaches the log and the LLM.
- The store is the local `~/.glimpse/events.jsonl` (`kind="click"`).

### E2E smoke (run by a human)

1. `cp config/allowlist.example.json ~/.glimpse/allowlist.json`; ensure a listed
   browser's bundle id is correct.
2. `./scripts/dev.sh`; grant Accessibility if prompted; relaunch.
3. Click several products in the allowlisted browser → `~/.glimpse/events.jsonl`
   gains `kind="click"` lines with OCR'd text.
4. Click in a non-allowlisted app (e.g. Notes) → **no** new `click` line appears.
5. Menu 👁 → "Today's Interests" → a grounded summary shows in the overlay within a
   few seconds, naming things you actually clicked.
6. Confirm no image files under `~/.glimpse/` and no phone numbers / long digit runs
   in the click lines (redaction).

## Phase 3 — auto-reply (synthetic input into WeChat)

Glimpse can fill or send a reply directly into WeChat's chat input box.
You calibrate the box position once; from then on suggestion cards have a
**填入 / 发送** button governed by an off-by-default **自动发送** toggle.

### Setup

WeChat's bundle ID must already be in `~/.glimpse/allowlist.json` (the same
list used for click-capture). Accessibility permission is also required (same
grant as Phase 2).

### E2E smoke (run by a human)

1. Menu 👁 → **设置输入框位置**, then click WeChat's message input box.
   Status shows "输入框已设置".
2. With auto-send **OFF** (default): pick a suggestion, click **填入**.
   - Expect: the reply text appears in WeChat's input box; nothing is sent.
3. Wrong-app refusal: bring another app to the front, click **填入**.
   - Expect: status shows "切换到微信再发送"; nothing is typed.
4. Enable menu 👁 → **自动发送**. The card button now reads **发送** (unless the
   suggestion is stale, where it stays **填入**).
5. Click **发送** with WeChat frontmost.
   - Expect: text fills, a red "发送中 5…4…3…2…1 按 Esc 取消" banner counts down,
     then Return is pressed and the message sends.
6. Repeat step 5 but press **Esc** during the countdown.
   - Expect: countdown aborts, message NOT sent, text left in the box.
7. Repeat step 5 but ⌘-Tab away before the countdown ends.
   - Expect: at zero, the send aborts (frontmost re-check); message NOT sent.
8. Confirm `~/.glimpse/events.jsonl` has `replied` records with modes
   `fill` / `sent` / `cancelled` matching the actions above.
