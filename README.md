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
mkdir -p ~/.glimpse
cp -r ../playbook/knowledge ~/.glimpse/knowledge   # OKF catalog (primary)
cp ../playbook/playbook.md ~/.glimpse/playbook.md  # legacy fallback
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

## Phase 4 — agentic core (evals)

The brain now drafts replies via a Claude tool-use agent (`glimpse_brain.agent.Agent`)
instead of a single LLM call. Quality is guarded by an offline, billable eval
suite (NOT part of `pytest`, since it makes real model calls):

    cd brain && PYTHONPATH=. ./.venv/bin/python -m evals    # needs ANTHROPIC_API_KEY

It runs each golden case in `brain/evals/cases/*.json` through the agent, applies
deterministic `must`/`must_not` checks plus an LLM-judge on subjective rubric
dimensions (grounded / tone / handles_uncertainty / safe), and prints per-case
PASS/FAIL + a summary. Add new cases by dropping a JSON file in that directory.

## Phase 5 — per-customer memory

The agent now remembers customers. Calibrate once (menu 👁 → **设置联系人区域**,
draw a box over WeChat's contact-name header); the detected name shows as
**👤 <name>** in the overlay and scopes memory to that customer.

- Interactions are auto-captured per customer; the agent can `recall_customer` and
  `remember_about_customer` while drafting.
- Memory is local-first (`~/.glimpse/palace`, MemPalace) and fail-soft — if it's
  unavailable, drafting falls back to knowledge-base-only.
- Config (`memory` table in `~/.glimpse/glimpse.toml`): `enabled`, `palace_path`,
  `embedding_model` (default `embeddinggemma`), `recall_k`.

### Memory integration test (opt-in, local, slow — downloads the embedding model)

    cd brain && ./.venv/bin/python -m pytest tests/test_mempalace_memory.py -m integration -v

### Manual E2E
1. 设置联系人区域 over the contact header → name appears as 👤 in the overlay.
2. Switch chats → the 👤 name changes.
3. Tell the agent something memorable about a customer, then return to that chat
   later → confirm it recalls the fact (check `recall_customer` in `events.jsonl`
   `agent_turn` tools_used).

## Phase 6 — the agent can see

When a customer sends a photo, the brain now sends a downscaled screenshot of the
conversation region (`OcrMsg.image`); the agent can call `look_at_conversation` to
see it (Claude vision — no extra model) and reason with the playbook + memory.

- The agent decides when to look, so vision tokens are spent only when a photo
  matters; everything is fail-soft to text-only.
- v1 sees the inline chat thumbnail (enough for an obvious product/damage). Precise
  SKU matching (a CLIP image index) and opening full-resolution images are later
  increments.

### Manual E2E
1. Send a product photo in WeChat's watched chat.
2. Confirm the agent calls `look_at_conversation` (in `events.jsonl` `agent_turn`
   tools_used) and drafts a reply that references what's in the photo.

## Feedback loop — rate, correct, and learn

Each suggestion card has 👍 / 👎 buttons; 👎 reveals a "更好的回复 / 修改建议"
field. Feedback is captured by the brain (model-free, fail-soft) into three places:
a redacted `feedback` audit event, a durable corpus (`~/.glimpse/feedback.jsonl`),
and — when a customer is known — that customer's memory (so the agent recalls the
correction next time). A rolling satisfaction metric emits a **dismissable advisory**
once quality is consistently high; it only *recommends* enabling 自动发送 and can
never flip the toggle itself (per-message quality still gates every send).

- Config (`feedback` table in `~/.glimpse/glimpse.toml`): `satisfaction_window`
  (default 20), `advisory_threshold` (0.90), `advisory_min_ratings` (20).

### Distilling feedback into eval cases (offline, billable)

Corrections become regression tests via an offline command (real model calls):

    cd brain && PYTHONPATH=. ./.venv/bin/python -m evals distill    # needs ANTHROPIC_API_KEY
    # review a generated candidate, then promote it into the gating set:
    cd brain && PYTHONPATH=. ./.venv/bin/python -m evals promote fb-<id>
    cd brain && PYTHONPATH=. ./.venv/bin/python -m evals             # now gates on it

`distill` turns each correction into a candidate case under
`brain/evals/cases/candidates/` (gitignored, **not** run by the default eval).
Only `promote` (a deliberate human step — review the distilled `must`/`must_not`
first) moves a case into `brain/evals/cases/`, where it gates future runs. `distill`
is idempotent: re-running skips corrections already turned into candidates.

### Manual E2E
1. Rate a suggestion 👍 and 👎; on 👎 type a correction and 提交.
2. Confirm `~/.glimpse/feedback.jsonl` gains a line per rating (note + conversation
   redacted) and `events.jsonl` has a `feedback` event.
3. With a customer set (👤), 👎+correction → return to that chat later and confirm
   the agent recalls it (`recall_customer` in `agent_turn` tools_used).
4. After enough 👍 (≥ `advisory_min_ratings` at ≥ `advisory_threshold`), a 💡
   advisory line appears and is dismissable — and the 自动发送 toggle does NOT change.

## Knowledge base — OKF catalog

Grounding lives in `~/.glimpse/knowledge/` as OKF docs (markdown + YAML
frontmatter: `id`, `title`, `type`, `tags`, `description`). The agent calls
`knowledge_base` to see the index, then `read_knowledge{id}` for the docs it
needs. Edit/add `.md` files there (nest into subfolders if you like — the catalog
is scanned recursively); changes are picked up live. If the directory is absent,
the agent falls back to the single `~/.glimpse/playbook.md`.
