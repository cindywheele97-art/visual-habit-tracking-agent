# Phase 4 (reshaped) — Agentic Core + Eval Harness (Design)

**Date:** 2026-06-14
**Status:** Approved (pending spec review)
**Keystone:** `2026-06-14-ai-native-architecture.md` (this is the first sub-project of that roadmap)
**Supersedes:** the standalone `2026-06-13-phase4-feedback-loop-design.md` (feedback
capture is deferred to the next slice; see Scope).

## Goal

Pivot the brain from a single grounded LLM call to a real **Claude tool-use
agent**, and stand up the **eval harness** that makes a non-deterministic,
customer-facing reasoning engine shippable. This is the minimal AI-native
substrate: the agent loop that P5/P6 hang `Memory` and `Vision` tools on, plus
the regression net that replaces determinism.

## Design Principles

- **AI-native, with evals as the safety net.** The model reasons and composes
  tools; the eval harness — not branch coverage — guards quality. (Conscious
  Rule 5 override per the keystone.)
- **Freeze the safety surface.** The agent emits the same `SuggestionsMsg`
  (multiple candidate drafts) as today, so the shell, overlay, and all Phase 3
  fill/send safety stay literally unchanged. Only the brain's internals change.
- **Provider-neutral seam.** The agent loop speaks neutral tool-call types; the
  Anthropic SDK specifics live behind a `ToolUseClient` impl, so OpenAI/other
  providers are a later drop-in (common path only — see Forward Compatibility).
- **Thin first slice.** The KB tool is deliberately simple (whole-file grounding,
  like today). P4's value is the agentic *loop scaffolding* + the *eval harness*,
  not yet smart retrieval.
- **Code checks what code can check** — even though the product overrides Rule 5,
  the eval harness's own plumbing stays deterministic; the model is used only for
  the genuinely subjective quality judgment.

## Scope

### In scope (v1)
- A Claude tool-use `Agent` loop replacing `Suggester` on the suggestion path.
- One registered tool: `KnowledgeBaseTool` (whole-file playbook + learnings).
- Provider-neutral `ToolUseClient` seam + `AnthropicToolUseClient` impl.
- An offline, opt-in, billable **eval harness**: hand-authored golden cases +
  hybrid scoring (deterministic `must`/`must_not` + LLM-judge for subjective
  dimensions) + a report.
- Minimal observability: an `agent_turn` audit event.

### Out of scope (deferred)
- **Feedback-capture UI** (👍/👎 + correction) and its wire messages — the next
  slice, once the agent + evals are proven and there is something worth rating.
- `Memory` and `Vision/SKU` tools (P5/P6). The `KnowledgeBase` interface is
  defined here; `Memory`/`Vision` are not.
- Smart KB retrieval (graphify graph, CLIP) — P6.
- Single-proposal UX — stays multi-candidate (revisit later).
- OpenAI/third-party client impls — only the seam is reserved (see below).

## Output shape (unchanged contract)

The agent returns up to `N` candidate drafts (`N` = existing
`llm.max_suggestions`), serialized to the same `SuggestionsMsg`. The overlay shows
them as cards with 复制/填入/发送 exactly as today. Rationale: keep the entire
shell + Phase 3 safety surface frozen while swapping the engine; and 2–3 options
(different tone/approach) are genuinely useful for human-in-the-loop CS.

## Architecture

New units in `brain/src/glimpse_brain/`:

| Unit | Responsibility |
|---|---|
| `tools.py: Tool` (Protocol) | `name: str`, `description: str`, `input_schema: dict`, `async run(input: dict) -> str`. |
| `knowledge.py: KnowledgeBase` (Protocol) | `grounding(query: str) -> str` — multimodal-ready signature (text now). |
| `knowledge.py: FileKnowledgeBase` | v1 impl: returns playbook (+ `learnings.md` if present), re-read each call. Fail-soft on missing files. |
| `tools.py: KnowledgeBaseTool` | Wraps a `KnowledgeBase` as a `Tool`. |
| `agent.py: Agent` | Owns the tool-use loop. `async suggest(tail: list[str]) -> list[str]` — same return type as `Suggester.suggest`. |
| `agent.py: ToolUseClient` (Protocol) | `async run_turn(system: str, messages: list[Msg], tools: list[ToolSpec]) -> AgentStep`. |
| `agent.py: AnthropicToolUseClient` | Prod impl over `anthropic` `messages.create(tools=...)`; translates SDK ↔ neutral types. Constructed lazily (tests never import anthropic). |

**Neutral types (the provider-agnostic seam):**
```python
@dataclass(frozen=True)
class ToolCall:   # one tool invocation the model requested
    id: str
    name: str
    input: dict

@dataclass(frozen=True)
class ToolResult:
    id: str       # matches a ToolCall.id
    output: str

@dataclass(frozen=True)
class AgentStep:  # the model's turn output: either calls to run, or a final answer
    tool_calls: list[ToolCall]   # empty when final
    final_text: str | None       # set when the model is done
```
The `Agent` works only in these types; `AnthropicToolUseClient` maps Anthropic
content blocks (`tool_use`) ↔ `ToolCall`/`AgentStep` internally.

**Loop:**
```
Agent.suggest(tail):
  messages = [user(conversation + "起草最多 N 条候选回复，只输出 JSON 字符串数组")]
  for _ in range(max_iterations):            # cap (config; default 4)
    step = await client.run_turn(AGENT_SYSTEM, messages, [kb_tool.spec])
    if step.final_text is not None:
        return _parse_suggestions(step.final_text, N)   # reuse existing parser
    # else: execute requested tools, append results, loop
    append assistant(step.tool_calls) to messages
    for call in step.tool_calls:
        out = await registry[call.name].run(call.input)
        append tool_result(ToolResult(call.id, out)) to messages
  raise SuggestionParseError("agent did not finalize")   # → existing degraded path
```

- `AGENT_SYSTEM`: instructs the agent that it is a 资深电商客服 agent, may call the
  knowledge-base tool to ground replies, must not fabricate beyond grounding, must
  treat the conversation as untrusted (ignore embedded instructions), and must
  end by emitting a JSON array of up to N candidate replies.
- `RateLimiter` caps agent **turns** per minute (as today); `max_iterations`
  bounds model calls within a turn. Over-budget/non-finalizing → degraded status
  (existing path) — no infinite loop, no UI hang.
- `server.py._on_ocr` calls `Agent.suggest` instead of `Suggester.suggest`; the
  return type and `SuggestionsMsg` emission are unchanged. The **summarizer keeps
  `LLMClient.complete`** — untouched.

## Eval Harness

`brain/evals/` (new):

**Golden cases** — `brain/evals/cases/*.json`:
```json
{ "id": "haggle-01",
  "conversation": ["客户: 能再便宜点吗？"],
  "rubric_focus": ["grounded", "tone", "handles_uncertainty"],
  "must": ["赠品|包邮"],
  "must_not": ["直接.*降价|好的.*便宜"],
  "notes": "议价：强调赠品/包邮，不直接降价" }
```
Coverage: 议价, 催发货, 售后, 政策问答, off-playbook (must say 需核实, not fabricate),
**OCR prompt-injection** (a smuggled "忽略以上指令" must be ignored), multilingual.

**Hybrid scoring:**
- Deterministic: `must`/`must_not` regex over the agent's drafts — cheap, reliable.
- LLM-judge (capable model + rubric prompt → structured JSON, per-dimension
  pass/fail + rationale) for subjective dimensions: `grounded`, `tone`,
  `handles_uncertainty`, `safe` (ignored the injection).

**Runner:** `python -m glimpse_brain.evals [--cases DIR]`. For each case: run
`Agent.suggest` → drafts → deterministic checks + judge → aggregate. Emits a
report (per-dimension pass-rate + failing cases with rationale). **Real billable
calls → a separate opt-in suite, never part of `pytest`** (repo rule: tests never
import anthropic). This billable suite is the non-determinism quality gate.

**Observability:** `server` logs an `agent_turn` event to `events.jsonl`
(tools called + draft count; no draft content, or redacted) — enough to trace.

## Forward Compatibility (reserved seams, not built)

- **Other LLM providers (OpenAI / third-party frameworks):** reserved via the
  `ToolUseClient` protocol + neutral `ToolCall`/`AgentStep`/`ToolResult` types.
  A future `OpenAIToolUseClient` is a new impl + SDK translation, **zero `Agent`
  changes**. *Caveat:* the seam covers the common path (messages + tools →
  tool_calls | text), not every provider feature (prompt caching, parallel calls,
  JSON modes, streaming) — those need per-impl handling. Reserving the interface
  is ~free; a universal zero-cost swap is not promised.
- **More tools (`Memory`, `Vision`):** reserved via the `Tool` protocol + the
  registry in the loop. P5/P6 register new tools; the loop is unchanged.

## Error Handling

| Condition | Behavior |
|---|---|
| Agent never finalizes within `max_iterations` | Raise `SuggestionParseError` → existing degraded status; no loop/hang |
| Final text not parseable as a JSON array | `SuggestionParseError` → degraded (existing path) |
| Tool `run` raises | Return an error string as the tool result so the model can recover; if it still can't finalize, degrade |
| `RateLimiter` denies | `CostCapExceeded` → existing handling |
| `learnings.md`/`playbook.md` missing | `FileKnowledgeBase` returns what exists / a sentinel; never errors the suggestion path |
| Conversation contains injected instructions | `AGENT_SYSTEM` instructs the model to ignore them; an eval case asserts this |

## Testing

### Unit (normal `pytest`, fakes only — no API)
- **`Agent` loop** (scripted fake `ToolUseClient`): runs a requested tool, feeds
  the result, terminates on `final_text`, parses drafts; respects
  `max_iterations` (never-finalizing script → degrades, no infinite loop);
  garbage final text → `SuggestionParseError`. *Intent: the non-deterministic
  engine's loop terminates, runs tools, and never runs away.*
- **Neutral translation:** `AnthropicToolUseClient` maps a constructed
  Anthropic-shaped response object ↔ neutral types (no network).
- **`FileKnowledgeBase` / `KnowledgeBaseTool`:** returns playbook(+learnings);
  missing file fail-soft; `input_schema` valid.
- **Eval-harness mechanics** (fake agent → canned drafts, fake judge → canned
  scores): case loading/validation, deterministic `must`/`must_not`, score
  aggregation, pass/fail threshold, report generation. *Intent: the eval's own
  plumbing is correct and deterministic.*
- **Server integration:** `_on_ocr` drives the `Agent` (fake `ToolUseClient`) and
  still emits the same `SuggestionsMsg` contract; `agent_turn` event logged.

### Billable eval suite (opt-in, real models)
The golden cases + judge — run manually/CI-gated, not in unit runs. The quality
gate for the agent.

### Migration
Swapping `Suggester` → `Agent` updates `test_suggester.py` and `test_server.py`
(inject a fake `ToolUseClient` instead of a `.complete()` fake). Expected.

**Coverage target:** ≥80% on the deterministic units (Agent loop, neutral types,
tools, knowledge, eval mechanics, server integration). The billable eval suite is
the separate quality gate.
