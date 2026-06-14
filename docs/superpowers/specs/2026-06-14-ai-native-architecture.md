# VHTA — AI-Native Architecture & Roadmap

**Date:** 2026-06-14
**Status:** Approved decomposition (keystone doc; each phase gets its own spec → plan)
**Supersedes/reshapes:** the standalone Phase 4 feedback-loop spec
(`2026-06-13-phase4-feedback-loop-design.md`) — its feedback-capture is folded
into the reshaped P4 below as an eval-case + memory-write signal.

## Purpose

This is the keystone architecture document for evolving VHTA from a
single-LLM-call CS-assist tool into an **AI-native** assistant. It defines the
target architecture, the stable interfaces, the subsystem decomposition, and the
phase roadmap. It is intentionally *not* a single implementation plan — each
phase below is brainstormed into its own spec and plan.

## Foundational decision: AI-Native

The product is built **AI-native**: the model is the reasoning core that
perceives the situation, decides what to retrieve, composes tools, and drafts —
and the system improves by improving **context, memory, tools, and evals**, not
by adding branch logic. Behavior *emerges* from model + memory + tools.

**This consciously overrides the operator's standing Rule 5** ("don't use the
model for routing; if code can answer, code answers") *for this product*. Rule 5
optimizes for determinism and cost; AI-native deliberately trades that for
emergent capability and adaptiveness, because **accumulated tacit knowledge
(默会知识) is the moat** and a deterministic pipeline cannot accumulate it the way
a reasoning agent can. The safety net that *replaces* determinism is **evals**
(see Pillars), not branch coverage.

**Non-negotiable counterweight:** AI-native changes how a reply is *reasoned*,
never whether the *irreversible send* is guarded. The Phase 3 safety
architecture (human confirm / 5s Esc-cancellable countdown / fail-closed
frontmost-app check / advisory-gated auto-send) stays unchanged. The agent
**proposes**; the human/countdown still **gates** sending.

## What stays vs. what changes

- **Shell (Swift) — role unchanged.** Region capture, OCR, click sensor, overlay,
  synthetic input + the Phase 3 send-safety gates. The agent's drafts flow to the
  overlay exactly as suggestions do today; sending stays gated.
- **Brain (Python) — internals become agentic.** The single grounded
  `Suggester` call is replaced by an **agentic core** (Claude tool-use loop) that
  composes tools. The shell↔brain NDJSON IPC is unchanged.

## Architecture pillars

| Pillar | What it is | Backing |
|---|---|---|
| **Agentic Core** | Reasoning engine: perceives → composes tools → drafts a reply *proposal* | Claude Agent SDK / tool-use loop; **Opus 4.8** reasoning, **Haiku** sub-steps |
| **Tools** (composable) | `KnowledgeBase`, `Memory`, `Vision/SKU` — each a clean tool contract | the seams below, exposed as tool definitions |
| **Memory** | Per-entity temporal **graph** (customer ↔ order/SKU ↔ issue), **agent-written** | MemPalace behind a thin `Memory` interface |
| **KnowledgeBase** | Stable curated knowledge (catalog, policy, 话术, learnings, SKU images) | playbook+learnings now → graphify graph + CLIP SKU index |
| **Eval harness** | Rated cases + regression scoring — the safety net for non-determinism | new; fed by feedback ratings |
| **Observability** | Trace what the agent perceived / retrieved / remembered / drafted, and why | new |
| **Safety layer** | Gates the irreversible send | Phase 3 (built, unchanged) |

```
SHELL (Swift): capture · OCR · click · overlay · synthetic input + SAFETY GATES
        │  IPC (NDJSON)
BRAIN (Python):
        ┌──────────────── AGENTIC CORE (Claude tool-use, Opus 4.8) ───────────────┐
        │  perceive (conversation + images + customer id) → compose tools → draft │
        └──────┬──────────────────┬───────────────────────┬──────────────────────┘
          tool │             tool │                  tool │
        ┌──────▼─────┐   ┌────────▼────────┐      ┌───────▼────────┐
        │KnowledgeBase│  │ Memory (graph,   │      │ Vision/SKU      │
        │             │  │ agent-WRITTEN)   │      │ (image → SKU)   │
        └─────────────┘  └──────────────────┘      └────────────────┘
        ┌── EVAL HARNESS ──┐     ┌── OBSERVABILITY ──┐
```

## Stable interfaces (tool contracts)

These are the seams that survive every backend swap. Each is a Python Protocol in
the brain, exposed to the agent as a tool.

- **`KnowledgeBase.grounding(query) -> Grounding`** — `query` is multimodal-ready
  (text now; image-bearing later). Returns grounding text + structured references
  (SKUs, policies). v1 impl returns whole-file playbook + learnings (today's
  behavior); later impls do retrieval (graphify graph, CLIP SKU match) behind the
  *same signature*. The key forward-compat detail: the signature takes the query
  now, even though v1 ignores most of it.
- **`Memory.recall(entity, query) -> list[MemoryHit]`** and
  **`Memory.remember(entity, content, relations)`** — entity-type-agnostic
  (customer/order/SKU/issue now; operator/"you" later is a new entity type, not a
  redesign). MemPalace-backed (read via `searcher.search_memories`, write via a
  `BaseSourceAdapter`).
- **`Vision.recognize(image) -> Recognition`** and
  **`SkuIndex.match(image) -> list[SkuHit]`** — visual recognition + similarity
  match over product images (CLIP/SigLIP). Added in P6.

## Verified backend research (spike facts, not README claims)

- **MemPalace** (55.5k★, MIT, Python): **adopt-as-library candidate** for Memory.
  Verified on this Apple-Silicon machine: install 58s; venv ~291 MB + default
  MiniLM model 79 MB (multilingual embeddinggemma-300m is +~300 MB and better for
  Chinese); no segfault; **in-process `searcher.search_memories` warm latency
  ~72 ms**; clean structured hits; Chinese semantic retrieval produced sensible
  signal (0.89 on an on-topic 议价 query). Read path embeddable; write path via
  `BaseSourceAdapter`. Beta with fast churn → **pin the version**. Adopt **behind
  the thin `Memory` interface** so we can swap/port if the footprint or churn bites.
- **Graphify** (66.7k★, MIT, Python; same project as the local `/graphify` skill):
  reference + **offline tool** for KB-graph construction (multimodal ingestion via
  LLM captioning, Leiden communities). Not a real-time visual SKU matcher; CLI/MCP
  ergonomics, not an inline library.
- **EvoAgentX** (3.1k★, **NOASSERTION license**): **reference only** — borrow the
  self-evolution/eval ideas; do not vendor (license + framework weight).
- **Gap:** real-time **visual SKU/product-image matching** (customer photo → SKU)
  is not provided by any of the three; it needs a dedicated **CLIP/SigLIP image
  index** we build ourselves (P6).

## Roadmap (each phase = own spec → plan → implement; thin shippable slice + evals)

| Phase | Ships | Rationale |
|---|---|---|
| **P4 (reshaped): Agentic Core + Eval harness** | Replace the single-call `Suggester` with a minimal Claude tool-use agent + the **Tool interface**, starting with the existing KB as its one tool. Stand up the **eval harness** (rated cases, regression scoring). Fold the original Phase 4 feedback-capture in as: rating → eval case + memory-write signal. Define `KnowledgeBase` + `Memory` interfaces (`Memory` trivial impl). Safety gates unchanged. | The AI-native pivot, minimal. A non-deterministic customer-facing agent can't ship without evals + the safety gates — so they land first, together, before any heavy integration. |
| **P5: Memory subsystem (the moat)** | Adopt MemPalace behind `Memory`; model customer ↔ order/SKU ↔ issue **graph**; agent **reads and writes** it. Evals extend to memory-grounded cases. | The tacit-knowledge moat; compounds with time → start accumulating early. (Recommended default; swap with P6 if 售后-photo pain is more acute now.) |
| **P6: Multimodal KB (SKU + image)** | `Vision`/`SkuIndex` tools (CLIP index over product images) + a graphify-built product graph in the KB. Agent handles 售后 photos. | Concrete CS capability gap; bounded; behind already-proven tool seams. |
| **P7 (later): Personal-assistant generalization** | Operator ("you") as a memory entity; resident-companion surface (vibe-Island interaction form). | Substrate already supports it (`Memory` is entity-agnostic). |

**Sequencing approach:** *agentic core + evals first (thin), then deepen tools.*
This de-risks the riskiest new thing — the agentic + eval pivot — with minimal
new dependencies, then adds Memory and Multimodal behind proven tool interfaces.
(Rejected alternatives: substrate-first, which validates the agent loop too late;
and full vertical slice on one scenario, which touches every subsystem at once.)

## Costs accepted (eyes open)

- Higher per-interaction latency + token cost (multi-step model calls).
- Non-determinism → **evals are mandatory**, not optional; unit tests alone can't
  guard a customer-facing reasoning agent.
- Stronger dependence on top-tier model capability (Opus reasoning core).
- New heavy dependency if MemPalace is adopted (~370–670 MB) — gated behind the
  `Memory` interface so it's reversible.

## Out of scope (for this keystone doc)

- Per-phase detailed designs (each phase has its own spec).
- Adopting an agent *framework* (EvoAgentX) — we build on Claude's native tool-use.
- Autonomous sending — the agent never sends without the Phase 3 human/countdown gate.

## Next step

Brainstorm **P4 (reshaped): Agentic Core + Eval harness** into its own spec, then
plan, then implement via subagent-driven TDD — the same loop used for Phase 3.
