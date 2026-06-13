# Phase 2 — Implicit-Context Collector (slice 1) — Design Spec

**Date:** 2026-06-12
**Status:** Approved by user (brainstorming session, section-by-section)
**Project:** Visual Habit Tracking Agent (formerly Glimpse), repo `~/Projects/visual-habit-tracking-agent`
**Builds on:** Phase 1 v1 (`2026-06-11-glimpse-v1-design.md`) — code-complete, merged to `main`.

## 1. What this is

The first slice of Phase 2: capture *what the user clicks* in opted-in apps and, on
demand, produce an LLM-generated **interest summary** of the day's behavior
("today you clicked 3 Adidas runners in Chrome, lingered on return policy, never
clicked Nike"). This turns the project's name — habit/behavior tracking — into a
visible, verifiable output.

It is **additive** to v1: it reuses the ScreenCaptureKit capture, Vision OCR, the
UDS IPC, the append-only JSONL event log, redaction, the overlay, and the LLM
client. Same two-process model (Swift shell + Python brain), same socket.

**Instrument:** event-triggered visual snapshots — a snapshot+OCR fires *at the
moment of a click*, not continuous recording. Continuous visual recording is
explicitly not used.

## 2. Decisions log

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| 1 | First observable output | On-demand interest summary (overlay) | Proves the captured signal is meaningful; forces the pipeline end-to-end; clean success criterion |
| 2 | Sensor for the slice | CGEventTap click + visual snapshot | Reuses ALL of v1's capture+OCR; universal (native apps too); one new permission; matches the pure-vision identity |
| 3 | Privacy bound | App allowlist | Smallest surface; capture runs only where opted in; non-allowed clicks read no pixels at all |
| 4 | Interpretation timing | Cheap per-click capture; one LLM call at summary time | CLAUDE.md Rule 5/6 — no per-click model calls; batch interpretation is cost-correct |
| 5 | Dwell/hover | Deferred | A click is already a strong interest signal; dwell is a clean additive follow-up |
| 6 | Context store | The existing `events.jsonl` (no new DB) | YAGNI; the summarizer reads the log directly |

Rejected for this slice: browser extension (separate deliverable — extension build,
native-messaging bridge, store packaging; browser-only; doesn't reuse the visual
pipeline), capturing everywhere (fail-open PII risk), region-scoped capture (misses
whole-window browsing), live comparison hints (needs low-latency inference + entity
resolution — too large as the first slice).

## 3. Architecture

Additive components only; everything else is v1, unchanged.

```
┌─ Swift shell (adds) ─────────────────┐      ┌─ Python brain (adds) ────────────┐
│ ClickSensor (CGEventTap, listen-only)│      │ click handler → event log        │
│   → allowlist check                  │ NDJSON│   (kind="click")                 │
│   → one-shot bounded capture@point   │◄────►│ summarizer (reads click events → │
│   → OCR → ClickMsg                   │ UDS  │   digest → 1 LLM call → summary)  │
│ AppAllowlist (~/.glimpse/allowlist)  │      │ shared LLM client (extracted)    │
│ menu "Today's interests"             │      └──────────────────────────────────┘
│   → SummarizeRequest                 │
│ overlay.showSummary(text)            │
└──────────────────────────────────────┘
```

### Swift shell (new)
- **`ClickSensor.swift`** — a `CGEventTap` (`.listenOnly`, `.leftMouseDown`) on the
  session event stream. On each click: read the click point and
  `NSWorkspace.shared.frontmostApplication?.bundleIdentifier`. If the bundle id is
  allowlisted: do a **one-shot bounded capture** of a region (~600×400 pt) centered
  on the click and clamped to the display, run existing `OCR.recognize`, and send a
  `ClickMsg`. If not allowlisted: do nothing — no capture, no OCR, no message.
  Listen-only: the tap never modifies or blocks events.
- **`AppAllowlist.swift`** — pure, testable. Loads `~/.glimpse/allowlist.json`
  (a JSON array of bundle-id strings, e.g. `["com.google.Chrome","com.apple.Safari"]`).
  `isAllowed(_ bundleId: String?) -> Bool`. Missing/malformed file → empty list
  (nothing captured) — fail-closed.
- **Capture-at-point** — a bounded one-shot BGRA frame of the computed sub-rect,
  captured via a short `CaptureEngine` session over that rect (reuses the hardened v1
  capture path; `SCScreenshotManager` one-shot is an acceptable fallback only if a
  short stream proves awkward for a single frame). No diff gate here — every
  allowlisted click captures exactly once, then OCRs.
- **Menu item "Today's interests"** in the existing status-bar menu → `SummarizeRequest`.
- **`overlay.showSummary(_ text:)`** — render a multi-line summary in the existing panel.

### Python brain (new)
- **Click handler** (in `server.py`) — on `ClickMsg`, append `kind="click"` to the
  event log with payload `{app, x, y, texts:[...]}`, redacted by the existing
  `Redactor` (same path as v1 payloads).
- **`summarizer.py`** — `summarize(events_path, now) -> str`. Reads the JSONL, keeps
  `kind="click"` records with `ts >= local-midnight(now)` and non-empty `texts`,
  assembles a compact digest (per-app grouped clicked snippets + counts), makes one
  LLM call grounded in that digest, returns the summary. No clicks → returns a fixed
  "No tracked activity yet today" string with **no** LLM call.
- **Shared LLM client** — extract `LLMClient`/`AnthropicLLM`/`RateLimiter` from
  `suggester.py` into a shared module so both the suggester and summarizer reuse them
  (no duplication, one rate cap).

### New protocol messages (mirrored Swift + Python, snake_case wire keys)
- `ClickMsg` (shell→brain): `{type:"click", ts, app, x, y, blocks:[Block]}`.
- `SummarizeRequest` (shell→brain): `{type:"summarize"}`.
- `SummaryMsg` (brain→shell): `{type:"summary", text}`.

The v1 wire contract is unchanged; these are new discriminated-union arms.

## 4. Data flow

**Per click:**
1. `CGEventTap` fires (left-mouse-down) → click point + foreground bundle id.
2. Allowlist check. Not allowed → stop, record nothing. Allowed → continue.
3. One-shot bounded capture (~600×400 pt around point, clamped) → OCR → blocks.
4. `ClickMsg` → socket → brain.
5. Brain redacts payload, appends `kind="click"` to `events.jsonl`. No image bytes
   ever leave the shell.

**On demand (summary):**
1. Menu "Today's interests" → `SummarizeRequest` → brain.
2. Summarizer reads `events.jsonl`, filters `kind="click"` since local midnight,
   builds the digest, one LLM call → summary text.
3. `SummaryMsg` → `overlay.showSummary(text)`.

## 5. Privacy boundaries and failure handling

**Hard privacy rules (each independently testable):**
- Non-allowlisted apps produce **zero** capture — pixels are never read, so there is
  nothing to leak.
- Raw images never persist or transmit (reuses v1's boundary); only redacted text
  reaches the log and the LLM.
- The store is the local `events.jsonl`; the on-demand summary is the only surfaced
  artifact.
- Allowlist fails closed: missing/malformed → capture nothing.

**Failure handling:**
- **Accessibility permission** (new, required for `CGEventTap`): if not granted, the
  tap fails to install → overlay shows "Accessibility needed for click tracking";
  v1 region-watch keeps working.
- Click sensor is `.listenOnly` — a bug can never block or alter user input.
- Empty/low-confidence OCR at a click → click recorded with empty `texts` (the act of
  clicking in-app is still signal); the summarizer skips empty-text clicks.
- `SummarizeRequest` with no clicks today → "No tracked activity yet today", no LLM
  call, no cost.
- Summarizer LLM error / cost cap → overlay degraded status (same as v1's path).

## 6. Event log (reused, one new kind)

Same envelope `{ts, kind, region_id, payload}`. New `kind="click"` with payload
`{app, x, y, texts:[...]}`. `region_id` is unused for clicks and is set to the empty
string `""`. No schema change — exactly the extensibility the v1 envelope was
designed for.

## 7. Testing

- **Summarizer (pytest, stubbed LLM):** synthetic `events.jsonl` → digest contains
  only `kind="click"` since local midnight; empty-text clicks excluded; redaction
  applied; grounding invariant (*clicked text appears in the prompt, un-clicked text
  does not*); cost-cap honored; no-clicks short-circuits with no LLM call.
- **Protocol (pytest + Swift):** `ClickMsg` / `SummarizeRequest` / `SummaryMsg`
  round-trip; snake_case wire keys match both sides.
- **Server (pytest, socket):** `click` → exactly one `kind="click"` log line;
  `SummarizeRequest` → `SummaryMsg` out.
- **Swift unit:** `AppAllowlist` matching (allowed / not-allowed / malformed /
  missing file → fail-closed); click-snapshot region math clamps at all four screen
  edges. `CGEventTap` itself is not unit-testable (permission/UI) → manual E2E.

## 8. Success criteria (done = all true)

1. Click a product in an allowlisted browser → a `kind="click"` event with the OCR'd
   text near the click lands in `events.jsonl`.
2. Click in a non-allowlisted app → no new click event (no pixels read, verified).
3. "Today's interests" → summary in the overlay within a few seconds, grounded in
   what was actually clicked (names clicked items, not un-clicked ones).
4. No raw images persisted or transmitted; click payloads redacted.
5. Idle cost ~0 — listen-only tap; capture fires only on an allowlisted click.

## 9. Out of scope for this slice

Dwell/hover capture (mouse-stationary snapshots), the browser extension (DOM click
targets, URLs, precise per-page dwell), live comparison hints, any persistent context
DB beyond the JSONL, scheduled/automatic summaries, multi-day trend analysis, and the
deferred `~/.glimpse` → product-name runtime rename. Each is a clean follow-up on this
same substrate.
