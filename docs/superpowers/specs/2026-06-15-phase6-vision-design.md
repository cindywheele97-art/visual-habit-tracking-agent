# Phase 6 — Multimodal Perception (Design)

**Date:** 2026-06-15
**Status:** Approved (pending spec review)
**Keystone:** `2026-06-14-ai-native-architecture.md` (P6 of that roadmap)
**Builds on:** P4 agentic core (the tool seam this extends), P5 memory (the agent
already composes KB + memory tools; vision is one more).

## Goal

Give the agent **eyes**. Today the brain sees only OCR'd text; when a customer
sends a product photo (most acutely in 售后), the agent gets garbled OCR or
nothing. This slice lets the agent *see* the conversation-region image via a
`look_at_conversation` tool, so it can recognize a product, see damage, or read a
screenshot, and reason about it with the playbook + memory it already has.

This is the **thin, foundational** multimodal slice. Precise SKU-ID matching (a
CLIP image index — P6b) and a graphify product graph (P6c) are richer increments
that build *on top of* a seeing agent, deferred until real usage shows they're needed.

## Design Principles

- **The agent decides when to look.** Vision tokens (real cost) are spent only
  when the agent calls `look_at_conversation` — consistent with how it already
  decides to recall memory or query the KB (AI-native).
- **No new dependency.** Claude is multimodal; the vision model *is* the agent's
  model. The shell already captures the image — it just discards it after OCR today.
- **Fail-soft.** Any vision failure degrades to text-only drafting (pure P5 behavior).
- **Multimodal-ready seam.** Extend the provider-neutral tool seam to carry image
  tool-results, so the next vision tool (P6b `match_sku`) reuses it for free.

## Scope

### In scope (v1)
- Shell sends a downscaled JPEG of the conversation region (when a frame likely
  contains a photo) as a new optional `OcrMsg.image`.
- Brain holds the latest image; a `look_at_conversation` tool returns it as an
  image tool-result; the agent gets the tool only when an image is available.
- Extend the neutral tool seam (`tooluse.py`) to carry image tool-results.

### Out of scope (deferred)
- **SKU visual matching** (CLIP/SigLIP index over the product catalog) — P6b.
- **graphify product knowledge graph** — P6c.
- **Opening full-resolution images** (synthetic click → full view). v1 sees the
  chat *thumbnail* as rendered inline — enough to reason about an obvious
  product/damage; full-res is a later increment.
- **Outbound images** (the agent sending a product/explanation image) — later.
- **PII redaction of images** — an image can't be meaningfully redacted and is the
  point; images reach Claude only when the agent looks (accepted, documented).

## Architecture

**Vision model = Claude itself** (the agent's model, `claude-sonnet-4-6`/Opus).
No separate vision model, no new dependency — the image enters the existing
tool-use loop as an image block.

### Shell (Swift)
| Unit | Responsibility |
|---|---|
| `ImageUtil` | Add downscale-to-max-dimension (~1024 px) + JPEG-encode + base64 helpers so the payload stays small over the local socket. |
| `processFrame` | Already holds the conversation-region `CGImage`. On a *likely-image frame* (region changed but OCR text sparse), attach `image` (base64 JPEG) to `OcrMsg`; a pure-text frame sends `""`. |

### Brain (Python)
| Unit | Responsibility |
|---|---|
| `protocol.py` | `OcrMsg.image: str = ""` (optional base64 JPEG; back-compat). |
| `tooluse.py` | `ToolImage(image_b64, media_type)` type; `Tool.run -> str \| ToolImage`; `ToolResult` gains optional `image_b64`/`media_type`; `_to_anthropic_messages` renders an image `ToolResult` as a `tool_result` with an Anthropic `image` block. |
| `vision_tool.py: LookTool` | `look_at_conversation` — returns the latest image as a `ToolImage`, or a "no image" text result when none. |
| `server` | Holds `self._last_image` (updated whenever an `OcrMsg` carries one); passes it to `agent.suggest`. |
| `agent.py` | `suggest(tail, customer=None, image=None)`; builds `LookTool` per turn when `image` is present (like the memory tools); the loop branches on `ToolImage` results. |
| `AGENT_SYSTEM` | "If the customer likely sent a photo (sparse/placeholder OCR, or they reference something you can't read), call `look_at_conversation` to see it, then reason with the playbook/memory." |

### The seam extension (minimal churn)
Existing text tools keep returning `str` — unchanged. Only:
- `ToolImage(image_b64: str, media_type: str)` new tiny type. `LookTool.run`
  returns it; every other tool still returns `str`.
- `ToolResult(id, output: str = "", image_b64: str = "", media_type: str = "")`.
- Agent loop: `result = await tool.run(...)`; `isinstance(result, ToolImage)` →
  image `ToolResult`, else text `ToolResult`.
- `_to_anthropic_messages`: image `ToolResult` →
  `{"type":"tool_result","tool_use_id":id,"content":[{"type":"image","source":
  {"type":"base64","media_type":mt,"data":b64}}]}`; text results unchanged.

## Data Flow

```
shell processFrame (diff-gated): OCR → blocks
  if likely-image-frame (region changed + sparse OCR text):
      image_b64 = base64(downscale+JPEG(CGImage))
  → OcrMsg{ blocks, contact, image: image_b64 or "" }
brain._on_ocr:
  if msg.image: self._last_image = msg.image       # hold latest image only
  ... existing tracker / capture / fire-on-new_inbound ...
agent.suggest(tail, customer, image=self._last_image):
  tools = base + (memory tools if customer) + (LookTool if image present)
  loop:
    agent calls look_at_conversation → LookTool.run → image ToolResult
      → _to_anthropic_messages emits an image block → agent sees the photo next turn
    agent reasons (KB + memory + image) → drafts → SuggestionsMsg (contract unchanged)
```

## Error Handling (all fail-soft)

| Condition | Behavior |
|---|---|
| Agent calls `look` but no image held | text result "（暂无可查看的图片）"; agent proceeds on text |
| Shell image encode/downscale fails | send no image (text-only frame); agent doesn't get the look tool |
| Oversized/garbage image; brain-side decode failure | downscale caps size; on failure drop → treat as no image |
| `look` not offered | `self._last_image` empty → tool not registered → agent can't call it pointlessly |
| Image staleness | `_last_image` is the latest received frame's image ("what's currently visible"); accepted for v1 |

## Testing

### Brain (pytest, fakes — no API)
- **`tooluse` (pure):** `_to_anthropic_messages` emits an `image` block for an
  image `ToolResult` and the text form for a text one; `ToolImage` round-trips.
  *Intent: the multimodal seam produces valid Anthropic content.*
- **`LookTool`:** returns a `ToolImage` carrying the held image; "no image" text
  result when none.
- **`Agent` (scripted client):** with an image available, a client that calls
  `look_at_conversation` → the loop builds an image `ToolResult`, feeds it back,
  the agent finalizes; `tools_used` includes `look_at_conversation`. Without an
  image → `LookTool` not offered (KB/memory only).
- **Protocol:** `OcrMsg.image` optional/back-compat round-trips Python ↔ Swift mirror.
- **Server:** holds `_last_image` from `OcrMsg`; passes it to `agent.suggest`;
  `LookTool` offered only when present.

### Shell (Swift Testing)
- `ImageUtil` downscale+JPEG yields a small base64 from a synthetic `CGImage`.
- The likely-image-frame heuristic: sparse-OCR + changed region → flag; rich text → no flag.

### Manual E2E (README checklist)
Send a real product photo in WeChat → the agent calls `look_at_conversation`,
sees it, and drafts a reply grounded in what it saw.

**Coverage target:** ≥80% on the pure brain units (seam, `LookTool`, agent vision
path, protocol, server) + the Swift `ImageUtil`. Real Claude vision is the manual seam.
