# Feedback-Capture Loop (Design)

**Date:** 2026-06-15
**Status:** Approved (pending spec review)
**Keystone:** `2026-06-14-ai-native-architecture.md` (the deferred feedback-capture slice)
**Builds on:** P4 agentic core, P5 memory, P6 vision — all merged to `main`.
**Supersedes:** `2026-06-13-phase4-feedback-loop-design.md` (the pre-pivot design,
on branch `phase4-feedback-loop`). This reframes feedback to feed **eval cases +
memory**, not a deterministic `learnings.md`.

## Goal

Let the human rate each suggestion 👍/👎 and correct it, and turn that signal into
the two things that actually improve an AI-native system: **memory** (the agent
recalls a correction next time it serves that customer) and **eval cases** (a
correction becomes a candidate golden case that, once promoted, guards against
regressions). Plus a **satisfaction advisory** that *recommends* — never enables —
auto-send when quality looks consistently high.

## Design Principles

- **Live capture is cheap and deterministic; distillation is offline and billable.**
  The live brain never calls the model for feedback — it captures, persists, writes
  memory, and moves a metric. The model enters only in an offline `evals distill`
  command. (Rule 5 is overridden for this product, but feedback *capture* needs no
  model — only the judgment step of turning a correction into an eval case does.)
- **The eval gate is protected.** A correction becomes a *candidate* case in a
  directory the default eval run never sees. Promotion into the gating set is a
  deliberate human act, so a brittle auto-distilled `must` regex can't block
  legitimate prompt/playbook improvements.
- **Advisory, never automatic.** A high satisfaction rate emits advisory *text*
  only. There is structurally no wire path from the advisory to the `自动发送`
  toggle. Reply quality gates sends per-message; an aggregate metric never does.
- **One consistent rule: everything persisted is redacted.** Audit events, the
  feedback corpus, and memory writes all pass through the existing `Redactor`.
- **Fail-soft everywhere.** A missing/corrupt corpus, a disk error, a model failure
  during distillation, or an unknown customer degrades gracefully — never an error
  on the suggestion path.

## Scope

### In scope
- Per-suggestion 👍/👎 in the overlay; 👎 reveals an optional correction note.
- `FeedbackMsg` (shell→brain) + `AdvisoryMsg` (brain→shell).
- Live brain on `FeedbackMsg`: redacted audit event; append to a durable
  `feedback.jsonl` corpus (with the conversation snapshot that prompted the draft);
  memory write on a correction when a customer is known; satisfaction advisory.
- `SatisfactionTracker` (pure rolling-rate core) + startup replay-seeding.
- Offline `python -m evals distill`: model-distill corrections from the corpus into
  candidate eval cases under `cases/candidates/`.
- Offline `python -m evals promote <id>`: move a candidate into the gating set.

### Out of scope (YAGNI / deferred)
- Auto-promotion of candidates into the gating set (deliberate by design).
- Any automatic enabling of auto-send.
- Embeddings/ranking/retrieval over feedback beyond what memory already provides.
- Editing candidates from the UI (they are files, reviewed on disk / in git).
- KB catalog construction (e.g. OKF-style) — a separate KB-track concern, not this slice.

## Rating shape

Binary 👍/👎. Satisfaction rate = 👍 / (👍 + 👎) over a rolling window. One tap,
unambiguous, and the only thing it drives (is auto-send trustworthy enough to
*recommend*?) needs a clean binary signal. The optional correction note carries the
"it was wrong, here's better" nuance a thumb can't — and is the seed for an eval case.

## Architecture

A split between a **live capture path** (brain, model-free) and an **offline
distillation path** (eval tooling, billable). Most logic is small, isolated units.

### Wire protocol (`protocol.py` + `Protocol.swift` mirror)
- `FeedbackMsg` (inbound):
  `{ type:"feedback", suggestion_id:str, region_id:str, verdict:"up"|"down", note:str="" }`
- `AdvisoryMsg` (outbound): `{ type:"advisory", text:str }`

### Brain
| Unit | Kind | Responsibility |
|---|---|---|
| `feedback.py: FeedbackRecord` | new | Frozen dataclass: `ts, suggestion_id, region_id, verdict, note, conversation:list[str], draft:str, customer:str`. |
| `feedback.py: FeedbackLog` | new | Append a `FeedbackRecord` to `~/.glimpse/feedback.jsonl` (conversation + note redacted on write); iterate records. Creates the dir if missing; skips on disk failure (warn). |
| `satisfaction.py: SatisfactionTracker` | new (pure core) | Rolling `deque(maxlen=window)` of `"up"/"down"`. `record(verdict)->bool` returns the rising-edge advisory trigger; `seed(verdicts)` fills from history with `advised = ready()`; computes `rate`/`ready`. |
| `server.py` | changed | Cache `_last_suggestions[region_id] = {tail, items}` whenever a `SuggestionsMsg` is sent. On `FeedbackMsg`: resolve `suggestion_id`→draft+tail snapshot; log redacted `feedback` event; append `FeedbackRecord`; if correction + known customer, `memory.write(...,"correction")`; `tracker.record(verdict)` → `AdvisoryMsg` on rising edge. At startup, replay `feedback` events to `tracker.seed(...)`. |
| `config.py` | changed | `BrainCfg.feedback_log` (default `~/.glimpse/feedback.jsonl`); `FeedbackCfg{satisfaction_window=20, advisory_threshold=0.90, advisory_min_ratings=20}`. |

### Eval tooling (offline, billable — `evals_pkg/`)
| Unit | Kind | Responsibility |
|---|---|---|
| `distill.py` | new | Pure: `record_to_prompt(record)` and `response_to_case(text, id)->dict` (schema-valid `EvalCase` body). Deterministic id `fb-<hash8>` over `(conversation, draft, note)`. |
| `__main__.py` | changed | `distill` subcommand: read corpus, keep `verdict=="down"` w/ non-empty note, skip ids already in `candidates/` or `cases/` (idempotent), one model call per new record → write `cases/candidates/fb-<hash8>.json`; per-record fail-soft. `promote <id>` subcommand: move `candidates/<id>.json` → `cases/<id>.json`. |

### Shell
| Unit | Kind | Responsibility |
|---|---|---|
| `Overlay.swift` | changed | Per-card 👍/👎; 👎 reveals a correction `TextField` + 提交; a dismissable advisory line (like the summary block); `onFeedback:(suggestionId, verdict, note)->Void`. |
| `main.swift` | changed | Wire `overlay.model.onFeedback` → `ipc.send(FeedbackMsg)`; handle `.advisory` → `overlay.showAdvisory(text)`. |

### The eval gate, by directory structure
`load_cases(directory)` globs `*.json` **non-recursively**. Therefore:
- `brain/evals/cases/*.json` — the gating set (`python -m evals` runs these). Unchanged.
- `brain/evals/cases/candidates/*.json` — generated candidates; the non-recursive
  glob never picks them up. `candidates/` is gitignored (machine-generated scratch).
Promotion = move the file up one directory (`evals promote <id>`, or a plain `git mv`).

## Data Flow

### 👍 (no note)
```
Overlay.onFeedback(id,"up","") → FeedbackMsg{verdict:"up"}
  → server: resolve snapshot; events.append("feedback",…,{verdict:"up",note:""})
          ; FeedbackLog.append(record with verdict="up")
          ; tracker.record("up") → if rising edge: AdvisoryMsg
  → shell handle(.advisory) → overlay.showAdvisory(text)   # dismissable; never flips toggle
```

### 👎 + correction note (current customer known)
```
👎 → card reveals "更好的回复" field → 提交 → FeedbackMsg{verdict:"down", note}
  → server: resolve snapshot (draft + tail)
          ; events.append("feedback",…,{verdict:"down", note:<redacted>})
          ; FeedbackLog.append(record: conversation+note redacted, draft, customer)
          ; memory.write(customer, redacted("（人工修正）…"+note), "correction")
          ; tracker.record("down")   # lowers rate; no advisory
```

### Offline distillation → candidate eval case
```
python -m evals distill
  → for each corpus record verdict=="down" & note!="" & id not already present:
        prompt = conversation snapshot + rated draft + human correction
        case = response_to_case(model(prompt), "fb-<hash8>")   # {rubric_focus,must,must_not,notes}
        write cases/candidates/fb-<hash8>.json
python -m evals promote fb-<hash8>      # human reviews, then moves into the gating set
```

### Startup replay (seeding the metric)
```
brain boot → read events.jsonl, filter kind=="feedback", take last `window`
             verdicts → tracker.seed(verdicts)   # advised = ready()
```

### Next suggestion uses the correction (no extra round-trip)
```
New DM from the same customer → agent.suggest(tail, customer=…)
  → the agent may call recall_customer → the "（人工修正）…" memory grounds the reply
```

## Error Handling & Guardrails

| Condition | Behavior |
|---|---|
| Corpus / memory append disk failure | log warn, swallow; the audit event is still recorded |
| 👎 with empty note | allowed — moves the metric, writes a corpus record, no memory write, no distill candidate |
| No current customer | no memory write; corpus + advisory still happen |
| `suggestion_id` not in the snapshot cache | corpus record written with empty conversation/draft; verdict still counts for the metric (never lose a vote) |
| `FeedbackMsg` unknown verdict | rejected by `Literal["up","down"]` → existing bad-message path |
| `events.jsonl` missing/corrupt at seed | empty window; no advisory until enough live ratings |
| distill: corpus missing / per-record model or parse failure | empty result / skip that record; idempotent re-runs |
| Advisory firing | emits text only; structurally no wire path to the toggle |
| Persisted content | conversation, note, and memory all redacted via the existing `Redactor` |

## Redaction

One rule: everything persisted is redacted. The `feedback` event payload redacts
the note (as all events). The `feedback.jsonl` corpus redacts both the conversation
snapshot and the note. The memory write is redacted (P5 rule). Redaction tokenizes
PII (names/phones), not product/policy terms, so a correction like
"应强调七天无理由退货" survives intact and still distills into a useful pattern. The
raw note exists only transiently in the in-memory `FeedbackMsg`. (This is a
deliberate change from the superseded design, which kept `learnings.md` raw as
user-authored config; corrections are now *data*, so the persist-redacted rule applies.)

## Testing

### Brain (pytest, fakes — no API)
**`SatisfactionTracker` (pure — exhaustive):**
- below `min_ratings` → never ready even at 100% — *no advising off thin evidence*
- reaching ≥threshold over ≥min_ratings → `record` returns `True` once (rising edge),
  `False` on subsequent 👍s — *non-nagging*
- a 👎 dropping below threshold re-arms; rising again fires again — *edge re-trigger*
- verdicts older than the window age out — *recent quality counts*
- `seed()` sets `advised = ready()` so no immediate re-advisory on restart

**`FeedbackLog`:** append→iter round-trips; conversation+note redacted in the written
record; missing dir created; disk failure swallowed.

**`distill.py` (fake client):** a correction record → a schema-valid candidate case
with `must`/`must_not`/`rubric_focus`; deterministic id; an id already present is
skipped (idempotent); a per-record model/parse failure is skipped, others proceed.

**`server` integration:** 👎+note with a current customer → `events.jsonl` has the
redacted `feedback` record **and** `feedback.jsonl` has a corpus record **and** a
memory write occurred; a sequence of 👍s crossing the threshold → `AdvisoryMsg` over
the socket; the snapshot cache resolves `suggestion_id`→draft.

**Protocol:** `FeedbackMsg` round-trips + rejects bad verdict; `AdvisoryMsg`
serializes single-line.

### Shell (Swift Testing)
`FeedbackMsg` encodes snake_case; `AdvisoryMsg` decodes via `Wire`. Overlay/menu
wiring (👍/👎, note field, advisory display + dismiss) is the manual **E2E** seam,
added to the README checklist (consistent with Phase 3).

**Coverage target:** ≥80% on the pure/brain units (`SatisfactionTracker`,
`FeedbackLog`, `distill`, protocol, server integration). UI is the deliberate
manual-tested seam.
