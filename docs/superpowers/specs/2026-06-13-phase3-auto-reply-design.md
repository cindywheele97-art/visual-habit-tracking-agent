# Phase 3 — Computer-Use Auto-Reply (Design)

**Date:** 2026-06-13
**Status:** Approved (pending spec review)
**Depends on:** v1 (CS-assist) and Phase 2 (click → interest summary), both merged to `main`.

## Goal

Close the core CS loop. Today the pipeline runs OCR → identify DM → playbook →
*suggestion*, then stops: the human copies/retypes the reply manually. Phase 3
turns a chosen suggestion into text placed in (and optionally sent from) the real
chat input box — the "利用 Computer Use 模拟人工进行回复" goal.

This is the most safety-sensitive feature in the product: it acts on real
customers/friends on the user's behalf. The design is therefore biased toward
**human-in-the-loop by default** and **fail-closed** refusals.

## Design Principles

- **Quality over speed.** The product optimizes for good replies, not fast ones.
  Nothing in this feature rushes the human. The auto-send countdown is a
  deliberately unhurried review window (5s), not a race.
- **Safe by default.** Fill-only is the default; auto-send is an explicit opt-in.
- **Fail closed.** Any uncertainty (wrong app, uncalibrated, no permission, stale)
  refuses the action and posts no synthetic event.
- **Human owns the irreversible act.** In the default mode the human presses Enter.
  In auto-send mode the 5s countdown is the abort window.

## Scope

### In scope (v1)
- One-time calibration of the chat input-box location.
- Fill-only path: place the chosen reply into the real input box (default).
- Auto-send path: fill + press Enter, behind an off-by-default global toggle,
  guarded by a 5s Esc-cancellable countdown and a stale-block.
- Fail-closed frontmost-app check (reusing `AppAllowlist`).
- Audit logging of every fill/send to `~/.glimpse/events.jsonl`.

### Out of scope (v1, YAGNI)
- Message recall after a real send (WeChat's own 2-min recall is the manual fallback).
- In-overlay text editing of suggestions (edit in the real chat box instead).
- Multi-region / multi-chat send routing.
- Retry logic on synthetic-event post failure.
- Accessibility-tree (AXUIElement) field targeting — WeChat exposes no clean
  text-field element, so a calibrated click-point is used instead.

### Future (Phase 4, separate spec): Feedback loop
A reply-rating mechanism (👍/👎 or score + edit-suggestion entry, per the
Claude/GPT pattern) that improves suggestion quality over time and tracks a
satisfaction metric. When quality crosses a threshold it may **surface an
advisory** ("满意率已达标，可考虑开启自动发送") — but it MUST NOT automatically flip
the auto-send toggle. Auto-send stays a human decision because reply quality is
per-message, not aggregate: a high overall satisfaction rate does not make the
next message safe to send unattended. The `replied` audit events defined here are
a substrate this phase can build on.

## Architecture

Phase 3 is **shell-side Swift only**; the brain changes by ~one message type.
The dangerous decision logic is isolated in one pure, exhaustively-tested unit
(`SendPlanner`); the un-testable OS coupling (`SyntheticInput`) is a thin,
protocol-injected leaf.

| Unit | Kind | Responsibility |
|---|---|---|
| `InputBoxStore` | new (~30 LOC) | Persist the calibrated input-box point to `~/.glimpse/input-box.json` (CG point). Mirror of `RegionStore`. |
| `InputBoxCalibrator` | new | One-time single-click capture of the chat input-box location. Triggered by menu item **"设置输入框位置"**. Sibling to `RegionSelector`. |
| `SyntheticInput` | new (protocol + impl) | The Computer-Use primitive. Posts `CGEvent`s: click at a point, paste (⌘V), press Return. The only unit touching the OS event system. |
| `SendPlanner` | new (**pure**) | Decides what to do from `(frontmostAllowed, calibrated, accessibilityTrusted, autoSendOn, stale)` → `SendPlan` enum. All safety logic; zero OS coupling. |
| `Sender` | new (orchestrator) | Asks `SendPlanner`, then drives `SyntheticInput`, `Countdown`, and the audit log. |
| `Countdown` | new (~25 LOC) | 5s countdown with an injected clock; Esc / Cancel aborts. Testable without real time. |
| `AppAllowlist` | reuse | Frontmost-app fail-closed check (same list gating click-capture). |
| `Overlay` | changed | Card gains one button (填入 / 发送, driven by toggle + stale); countdown UI; `onFill` / `onSend` callbacks. |
| menu (`main.swift`) | changed | "设置输入框位置" item; "自动发送" checkbox toggle (off by default; also the kill-switch). |
| `protocol.py` / `Protocol.swift` | changed | New `replied` message (shell → brain): `{suggestion_id, region_id, mode}`. |

### `SendPlan` enum

```
enum SendPlan {
    case refuse(reason: RefuseReason)   // post nothing; surface reason in status
    case fill                           // click + paste, stop
    case fillThenSend                   // click + paste + countdown + Return
}
```

`fillThenSend` is returned **only** when `autoSendOn ∧ ¬stale ∧ frontmostAllowed
∧ calibrated ∧ accessibilityTrusted`. Every other combination is `.fill` or
`.refuse`.

### Permissions

Posting `CGEvent`s requires Accessibility trust (`AXIsProcessTrusted()`), which
the user already grants for click-tracking. **No new permission prompt.**

### Constants

- `Countdown.defaultDuration = 5` seconds (tunable; chosen for an unhurried,
  human-like review window — see Design Principles).

## Data Flow

### One-time calibration
```
Menu "设置输入框位置" → InputBoxCalibrator single-click overlay
  → user clicks WeChat's input box → InputBoxStore.save(point) → ~/.glimpse/input-box.json
```

### Fill-only path (toggle OFF — default)
```
User clicks 填入 on a card
  → Overlay.onFill(suggestionId, text)
  → Sender.handle(text, autoSendOn:false, stale:…)
      → SendPlanner.plan(...) → .fill   (or .refuse — see Error Handling)
      → NSPasteboard set to `text`
      → SyntheticInput.click(at: calibratedPoint)   // focuses field + confirms window
      → SyntheticInput.paste()                       // ⌘V
  → append replied{mode:"fill"} to events.jsonl; send `replied` to brain
  // text sits in WeChat's box; user reviews, edits, presses Enter themselves
```

### Auto-send path (toggle ON, NOT stale)
```
User clicks 发送 on a card
  → Sender.handle(text, autoSendOn:true, stale:false)
      → SendPlanner.plan(...) → .fillThenSend
      → pasteboard set → click(calibratedPoint) → paste()   // same fill as above
      → Countdown(5s) shown in overlay: "发送中 5…4…3…2…1  按 Esc 取消"
          ├─ Esc / Cancel            → abort: text left in box, replied{cancelled}, STOP
          ├─ frontmost app changed   → abort at Return-time, replied{cancelled}, STOP
          └─ elapses & app frontmost → SyntheticInput.pressReturn()
                                      → replied{mode:"sent"}; send `replied` to brain
```

### Auto-send blocked by staleness (toggle ON, `stale == true`)
```
SuggestionsMsg arrives with stale:true
  → Overlay renders the card button as 填入 (not 发送), even though toggle is ON
  → clicking runs the fill-only path; SendPlanner also returns .fill defensively
```

**Undo semantics:** fill-only is trivially undoable (text just sits in the box).
Auto-send's 5s countdown is the undo window; after Enter the message is genuinely
sent (recall is out of scope).

## Error Handling & Guardrails

All refusal logic lives in `SendPlanner` (pure, tested). Every refusal is
**fail-closed**: no synthetic event is posted, and a clear reason surfaces in the
overlay status line.

| Condition | Detection | Behavior |
|---|---|---|
| Wrong app frontmost | `AppAllowlist.isAllowed(NSWorkspace.frontmost…bundleId)` false | `.refuse` → "切换到微信再发送"; nothing posted |
| Input box not calibrated | `InputBoxStore.load()` is nil | `.refuse` → "先设置输入框位置"; nothing posted |
| Accessibility not trusted | `AXIsProcessTrusted()` false | `.refuse` → reuse existing "Accessibility needed" status; nothing posted |
| Suggestion stale + auto-send on | `SuggestionsMsg.stale` | Downgrade to `.fill`; never auto-sends |
| ⌘V / click / Return post fails | `CGEvent` post fails | Abort sequence; status "发送失败"; partial state left for manual fix |
| Countdown interrupted | Esc, Cancel, **or frontmost app changes mid-countdown** | Abort before Return; text left in box; `replied{cancelled}` |

Two deliberate extra safety details:
- **Re-check frontmost app at Return time, not just at fill time.** During the 5s
  countdown the user could ⌘-Tab away; if the chat app is no longer frontmost when
  the countdown elapses, abort instead of pressing Enter into whatever is now
  focused. This closes the one race the countdown otherwise opens.
- **Kill-switch = the "自动发送" toggle.** Flipping it off is one menu click away and
  immediately reverts every card to fill-only.

## Wire Protocol Change

New shell → brain message, mirrored in `protocol.py` and `Protocol.swift`:

```
replied { suggestion_id: str, region_id: str, mode: "fill" | "sent" | "cancelled" }
```

The brain appends it to the audit log (`events.jsonl`). Conversation tracking is
unaffected — the sent reply re-enters via OCR as a "我" line, as today. The
`replied` message exists for the audit trail a feature that acts on real people
requires, not for tracking.

## Testing

Per Rule 9 (tests encode *why*) and the codebase's protocol-DI + Swift Testing
conventions.

### `SendPlanner` (pure — safety core, exhaustive)
- refuses when frontmost app not allowlisted — *prevents pasting into the wrong app*
- refuses when input box uncalibrated — *prevents clicking a meaningless point*
- refuses when Accessibility untrusted — *cannot post events anyway*
- returns `.fill` (not `.fillThenSend`) when stale even with auto-send on — *stale-block*
- returns `.fillThenSend` only when autoSend ∧ ¬stale ∧ all checks pass — *the one send path*

### `Countdown` (injected clock, no real time)
- elapses → fires send callback exactly once
- Esc / cancel before elapse → fires abort, never send
- *intent: the abort window is real; send fires exactly once*

### `InputBoxStore`
- save → load round-trips the point
- missing / corrupt file → nil (fail-closed)

### `Sender` (with mock `SyntheticInput`)
- `.fill` plan → click + paste, **no** Return posted
- `.fillThenSend`, countdown elapses, app still frontmost → Return posted exactly once
- countdown cancelled OR app changed at Return-time → **no** Return posted
- *intent: Enter is only ever pressed under the full set of conditions*

### Brain
- `replied` message parses and appends to the audit log
- round-trips Swift ↔ Python wire

### `SyntheticInput` (real `CGEvent` posting)
Cannot meaningfully unit-test OS event injection → covered by a **manual E2E
checklist** (extend the existing E2E doc): calibrate → fill into real WeChat →
toggle on → auto-send with Esc-cancel → auto-send completing → wrong-app refusal.

**Coverage target:** ≥80% on the testable Swift units (`SendPlanner`, `Countdown`,
`InputBoxStore`, `Sender`) and the brain change. `SyntheticInput` is the
deliberate manual-tested seam.
