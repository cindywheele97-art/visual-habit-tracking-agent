# Glimpse v1 — Design Spec

**Date:** 2026-06-11
**Status:** Approved by user (brainstorming session, section-by-section)
**Working name:** Glimpse (rename freely; repo `~/Projects/glimpse`)

## 1. What this is

A persistent on-screen macOS agent that watches a user-selected screen region using
pure vision (screenshot → OCR), understands what it sees, and assists the user via an
always-on-top overlay.

**Long-term product:** a general visual agent that records user habits and click
behaviors ("implicit context") and integrates workflows. **v1 is one vertical
slice:** customer-service assist — watch a chat window, recognize new customer
messages, suggest grounded replies the user copies and pastes.

v1 exists to pressure-test the reusable core (capture → recognition → event log →
overlay) with a bounded, observable use case.

## 2. Decisions log

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| 1 | v1 vertical slice | Customer-service assist | Most bounded; clearest success criteria; exercises the full pipeline |
| 2 | Chat surface | Anything on screen (pure vision); native macOS apps are the concrete target | No per-app integration; matches the general-visual-agent end goal |
| 3 | Data boundary | Hybrid: local OCR + cloud LLM | Screenshots never leave the machine; only extracted text goes to the LLM |
| 4 | Reply grounding | Editable local script file (markdown 话术/policies) | Suggestions match the business from day one; no database |
| 5 | Audience | Personal tool first | No installer/onboarding/settings UI; config in files; optimize iteration speed |
| 6 | Stack | Swift shell + Python brain | Native where macOS forces it; Python where iteration lives; brain is portable |
| 7 | Synthetic input | None in v1 | Copy-paste only; no Accessibility-control permission; actions earn trust later |

Rejected stacks: all-Swift (future LLM/data work iterates too slowly), pure
Python/pyobjc (overlay + capture bridges are fragile — degrades the product's soul),
Electron/Tauri (capture/OCR still need native bindings; heavy runtime for an
all-day-idle tool).

## 3. Architecture

Two processes, one local Unix-domain socket, newline-delimited JSON.

```
┌─ Swift shell (menu-bar app) ─────────┐      ┌─ Python brain (daemon) ──────────┐
│ Region selector (drag, named,        │      │ IPC server (asyncio)             │
│   persisted)                         │ NDJSON│ Conversation tracker             │
│ Capture loop (ScreenCaptureKit ~1fps)│◄────►│ Suggestion engine (script file + │
│ Pixel-diff gate (OCR only on change) │ UDS  │   conversation tail → Claude API) │
│ OCR (Vision, accurate, zh-Hans + en) │      │ Event log (append-only JSONL)    │
│ Overlay panel (suggestions + status) │      │ Config (TOML; API key from env)  │
└──────────────────────────────────────┘      └──────────────────────────────────┘
```

### Swift shell — owns everything macOS forces to be native

- **Region selector:** drag-to-select, screenshot-crosshair style. Regions are named
  and persisted across restarts.
- **Capture loop:** ScreenCaptureKit stream of the selected region at ~1 fps.
- **Pixel-diff gate:** cheap frame comparison; unchanged frames are dropped before
  OCR. This is the all-day-idle battery story.
- **OCR:** Vision `VNRecognizeTextRequest`, accurate mode, languages zh-Hans + en.
  Raw pixels live and die inside this process.
- **Overlay panel:** non-activating, always-on-top panel; renders 1–3 suggestions,
  each with a copy button; status dot (green watching / amber degraded / red error).
- **IPC client:** sends `{ts, region_id, text_blocks, ocr_confidence}`; receives
  overlay commands; auto-reconnects and resends last unacknowledged payload.

### Python brain — owns everything intelligent (all future iteration happens here)

- **IPC server:** asyncio Unix-domain socket server.
- **Conversation tracker:** turns raw OCR snapshots into conversation state.
  Dedupes re-reads of a static screen at text level; classifies changes as
  *new inbound customer message* (triggers suggestions) vs own-reply appearance,
  scroll, or timestamp ticks (do not trigger).
- **Suggestion engine:** redaction pass → prompt assembly (script file + redacted
  conversation tail) → Claude API → 1–3 ranked suggestions.
- **Event log:** append-only JSONL; generic envelope (see §6).
- **Config:** one TOML file — regions, OCR languages, model id, prompt template
  paths, redaction regex list, cost cap. API key from environment only.

The socket protocol is the portability seam: phase-2/3 features and a future Windows
shell speak the same protocol to the same brain.

## 4. Data flow (the watch loop)

1. User selects the chat region once; shell captures at ~1 fps.
2. Pixel-diff gate: unchanged → frame dropped, nothing else runs. Changed → OCR →
   text blocks → socket → brain.
3. Brain dedupes at text level and updates conversation state. Only a genuinely new
   inbound message triggers the suggestion engine.
4. Suggestion engine redacts (configurable regex list: phone numbers, order IDs,
   etc.), calls the LLM with script file + redacted tail, returns suggestions; the
   overlay renders them.
5. User clicks copy and pastes into the chat app manually. v1 sends no synthetic
   clicks or keystrokes.

## 5. Privacy boundaries and failure handling

**Hard privacy rules (each independently testable):**
- Raw pixels never leave the Swift shell and never touch disk.
- Only redacted text reaches the LLM.
- The event log stores text and hashes, never images.

**Failure handling:**
- OCR confidence below threshold → the brain (which receives `ocr_confidence` with
  every payload) discards the snapshot before it reaches conversation state
  (prevents phantom "new messages").
- LLM timeout/error → amber status dot; last good suggestions stay visible marked
  stale; retry with backoff.
- Socket drop → shell auto-reconnects, resends last unacknowledged payload.
- Region lost (window closed/moved) → OCR yields ~nothing; after 2 minutes the
  status dot signals "region looks dead"; user re-selects manually. No auto-tracking
  in v1.
- Cost guard: hard cap on LLM calls per minute; when hit, amber dot and suggestions
  pause.

## 6. Event log schema (the phase-2 substrate)

Append-only JSONL. One generic envelope:

```json
{"ts": "...", "kind": "...", "region_id": "...", "payload": { }}
```

v1 kinds: `observation` (recognized text delta), `suggestion_shown`,
`suggestion_copied`, `error`. The envelope is deliberately not CS-specific: phase 2
adds kinds like `click` and `dwell` without schema surgery. Payloads pass the same
redaction as LLM calls before being written.

## 7. Testing

Correctness lives in the brain; tests concentrate there.

- **Conversation tracker (pytest):** synthetic OCR-snapshot sequences. Core
  invariant encoded as tests: *suggest exactly once per new inbound message* — no
  duplicates on re-OCR, no trigger on own-reply appearance, no trigger on scroll.
- **Suggestion engine (pytest, stubbed LLM):** grounding (script-file content
  present in prompt), redaction applied, cost cap honored.
- **OCR fixtures:** folder of real chat-window PNGs run through Vision OCR via a
  small Swift CLI target; catches OCR regressions on Chinese chat layouts.
- **End-to-end harness:** local HTML fake-chat page that posts messages on a timer;
  run the real pipeline against it. This is the repeatable success-criteria gate.

## 8. v1 success criteria (done = all true)

1. Suggestion visible ≤5 s after a new customer message appears in the region.
2. Zero duplicate suggestions for one message across re-OCRs.
3. Zero images persisted or transmitted (no image bytes outside the shell process).
4. Idle CPU under ~3% when the region is static.
5. Real-world gate: run against an actual chat for a day; copy at least one
   suggestion you'd genuinely send.

## 9. Roadmap (later phases — explicitly out of v1 scope)

- **Phase 2 — implicit-context collector:** reuses capture + OCR + event log; adds
  click/dwell observation (e.g., skipped Nike, clicked Adidas → context store).
- **Phase 3 — workflow island:** reuses overlay + IPC; adds terminal/CLI/IDE status
  feeds (vibe-Island-style).
- **Phase 4 (candidate) — computer-use actions:** synthetic input, only after the
  substrate has earned trust.

Each phase gets its own brainstorm → spec → plan cycle.

## 10. Out of scope for v1

Windows support, browser-extension DOM reading, accessibility-API text extraction,
synthetic input, installers/signing/notarization, settings UI, multi-region
simultaneous watch, auto region tracking, local LLM fallback.
