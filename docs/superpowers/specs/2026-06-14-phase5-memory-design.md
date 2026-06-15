# Phase 5 — Memory Subsystem (Design)

**Date:** 2026-06-14
**Status:** Approved (pending spec review)
**Keystone:** `2026-06-14-ai-native-architecture.md` (P5 of that roadmap)
**Builds on:** P4 agentic core (the agent + Tool seam this plugs new tools into).

## Goal

Give the agent **per-customer memory**: walking into a conversation, it knows
this customer's history and the operator's accumulated tacit notes about them.
This is the moat from the AI-native pivot — tacit knowledge (默会知识) that
compounds with time. Scope of this slice: **customer-keyed dossiers** (no
explicit cross-entity graph yet).

## Design Principles

- **Memory never breaks suggestions.** Every memory failure is fail-soft → the
  agent falls back to KB-only drafting (pure P4 behavior).
- **Hybrid capture.** Deterministic interaction capture (the safety net — nothing
  lost) + agent-distilled facts (the moat). The agent reasons over both.
- **Thin `Memory` interface.** MemPalace sits behind it; if the dependency weight
  or churn bites, we swap the impl without touching the agent.
- **Local-first.** The palace is on the operator's machine — effectively their
  local CRM. No customer data leaves except via the same redacted path the live
  conversation already uses.

## Scope

### In scope (v1)
- Contact-name identity: a calibrated header region OCR'd into a memory key,
  surfaced in the overlay for correction.
- A `Memory` Protocol + `MemPalaceMemory` impl (library, version-pinned).
- Hybrid capture: deterministic interaction writes + agent `remember` writes.
- Agent `recall_customer` / `remember_about_customer` tools (bound to the current
  customer), registered alongside `knowledge_base`.
- `embeddinggemma-300m` multilingual embeddings (Chinese domain).

### Out of scope (deferred)
- Explicit entity graph (customer ↔ order/SKU ↔ issue extraction + relationships)
  — the richer increment after dossiers prove valuable. MemPalace's internal
  `hallways`/`knowledge_graph` structure emerges during mining but we don't query
  it explicitly yet.
- Operator ("you") memory entity — P7.
- Multimodal memory (remembering images) — needs P6 vision first.
- Bulletproof identity (globally-unique IDs) — v1 keys on the contact name as
  shown, which is sufficient for one operator's customer set.

## Identity

Per-customer memory needs a stable key. The system is anonymous today. v1:
**OCR the contact name from a calibrated header region** and use it as the memory
key (MemPalace "wing"). When the operator switches chats, the name changes → the
agent automatically knows it's a different customer. The detected name is
**displayed in the overlay** so the operator sees who memory is scoped to and can
notice when it's wrong. Honest caveat: a display name is not globally unique
(duplicate names, renamed contacts); for one operator's customer set this is
almost always fine, and bulletproof identity is a later increment.

## Architecture

### Shell (Swift) — identity capture
| Unit | Responsibility |
|---|---|
| `ContactRegionStore` | Persist the calibrated contact-name region rect to `~/.glimpse/contact-region.json`. Mirror of `InputBoxStore`. |
| menu "设置联系人区域" | One-time calibration (draw the rect over WeChat's contact header), like the Phase 3 input-box calibration. |
| capture cycle | OCR the contact-name region alongside the conversation region; attach the detected name as a new optional `contact` field on `OcrMsg`. |
| overlay | Display the current detected contact name (the safety net). |

### Brain (Python) — the Memory subsystem
| Unit | Responsibility |
|---|---|
| `Memory` (Protocol) | `recall(customer, query, k) -> list[MemoryHit]`; `write(customer, content, kind)` with `kind ∈ {"interaction","fact"}`. The stable seam. |
| `MemPalaceMemory` | Implements `Memory` over MemPalace (library, pinned): wing = customer; recall via `searcher.search_memories(wing=customer, n_results=k)` (spike-verified ~72 ms); write via the path the **upfront spike** pins. |
| `recall_customer` / `remember_about_customer` tools | The agent's Memory tools, **bound to the current customer** (the brain injects the key from `OcrMsg.contact`; the agent only queries/writes "about the current customer"). Registered alongside `knowledge_base` in the P4 agent. |
| deterministic capture | When the tracker yields new interactions and a customer is known, the brain auto-`write`s them (kind `"interaction"`). |
| config | palace path (`~/.glimpse/palace`), embedding model (`embeddinggemma-300m`), recall `k`. |

**Embedding model:** `embeddinggemma-300m` (multilingual), not the MiniLM default
— the domain is Chinese and the spike showed embeddinggemma is materially better
for CJK. Cost: +~300 MB lazy-download (total footprint ~670 MB).

**Redaction & locality:** the palace is local-first. Stored interaction text is
run through the existing `Redactor` (phones etc.) on the way in; recalled content
is redacted again before it reaches the model — consistent with the live
conversation path. The customer name (the key) is kept.

## Data Flow

**One-time:** Menu 设置联系人区域 → draw rect → `ContactRegionStore.save(rect)`.

**Suggestion cycle:**
```
capture: OCR conversation region (existing) + OCR contact-name region
  → OcrMsg{ blocks, contact: "小明" }
brain._on_ocr:
  current_customer = msg.contact or None
  tracker.ingest → new interactions
  if current_customer and new interactions:
      memory.write(current_customer, redact(interaction), kind="interaction")
  if new_inbound: fire the agent (recall/remember tools bound to current_customer)
agent loop (P4 + memory tools):
  may call recall_customer(query) → memory.recall(current_customer, query) → redacted hits
  drafts grounded in KB + recalled memory
  may call remember_about_customer(fact) → memory.write(current_customer, fact, "fact")
  → drafts → SuggestionsMsg   (contract unchanged)
overlay: shows detected contact name + drafts
```
**Customer switch:** `OcrMsg.contact` flips → brain rebinds `current_customer`;
tools + capture rescope. **Fail-soft:** `contact` empty → `current_customer =
None` → memory tools not offered, capture skipped, agent drafts KB-only.

## MemPalace Mapping + Write-Path Spike

- wing = customer (name sanitized to a valid wing key; MemPalace has wing-naming
  rules — strip leading/trailing separators).
- room = `kind` (`interactions` / `facts`).
- drawer = one interaction or fact + metadata `{kind, ts}`.
- recall = `search_memories(query, palace_path, wing=customer, n_results=k)` →
  redact hits → return.

**The write path is the plan's first task (a spike).** Candidates, cleanest-first:
(a) a `BaseSourceAdapter` (MemPalace RFC-002 extension point); (b) lower-level
`palace.get_collection` + Chroma upsert with the embedder; (c) write content to a
temp file + `mine()` in-process. The spike picks the cleanest working path;
`MemPalaceMemory.write` hides it. If none is clean, that's a day-one signal to
reconsider (port — the fallback the thin interface reserves).

## Error Handling (all fail-soft)

| Condition | Behavior |
|---|---|
| MemPalace import/init fails | Memory disabled → KB-only drafting; logged once |
| `recall` fails | empty hits → agent drafts without memory |
| `write` fails | log + swallow; interaction still in `events.jsonl` |
| contact OCR empty/garbage | sanitize; empty → no customer → fail-soft |
| embedding model still downloading | memory disabled until ready (lazy/async) |
| odd chars in contact name | sanitize to a safe wing key |

## Testing

### Brain (pytest, fakes — fast, no model/network)
- **`Memory` contract via an in-memory fake**: `recall` returns what was written;
  **customer-scoping isolation** (小明's writes never recalled for 小红); `kind`
  filtering. *Intent: the dossier boundary is real.*
- **`recall_customer` / `remember_about_customer` tools** (fake `Memory`): bound
  to the injected current customer; the agent never sees the key. Extends the P4
  agent tests (scripted client calls the tool → hit lands in transcript; remember
  → a write is recorded).
- **Deterministic capture**: new tail + current customer → interaction written;
  no customer → nothing written.
- **Fail-soft**: `current_customer = None` → memory tools not registered, agent
  drafts KB-only (P4 path unchanged).
- **Redaction**: stored + recalled content run through `Redactor` (a phone number
  never reaches storage or the model).
- **Protocol**: `OcrMsg.contact` round-trips Python ↔ Swift mirror; optional/
  back-compatible (old shells omitting it still parse).

### Shell (Swift Testing)
`ContactRegionStore` save/load round-trip + fail-soft on missing file (mirrors
`InputBoxStore`).

### MemPalace integration (opt-in, real local palace)
`MemPalaceMemory` against a **temp palace**: write a fact, recall it, assert it
returns scoped to its wing. Gated out of the normal `pytest` run (downloads the
embedding model, slow), like the eval suite. This validates the write-path spike.

### Manual E2E (README checklist)
Calibrate the contact region → see the detected name in the overlay → switch
chats and watch it change → confirm the agent recalls a prior fact about a
returning customer.

**Coverage target:** ≥80% on the pure brain units (`Memory` fake-backed logic,
tools, capture, fail-soft, redaction, protocol). `MemPalaceMemory` is covered by
the opt-in integration test; UI is the manual seam.
