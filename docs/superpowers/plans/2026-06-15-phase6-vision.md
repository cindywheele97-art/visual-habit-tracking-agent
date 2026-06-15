# Phase 6 — Multimodal Perception Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the agent eyes — the shell sends a downscaled conversation-region JPEG as `OcrMsg.image`; the brain holds the latest and offers a `look_at_conversation` tool that returns it as an image tool-result, so the agent (Claude vision, no new dependency) sees customer photos and reasons with KB + memory.

**Architecture:** Extend the provider-neutral tool seam (`tooluse.py`) to carry image tool-results (`ToolImage` + image `ToolResult` + an Anthropic image block in `_to_anthropic_messages`). A `LookTool` returns the held image; the agent registers it per-turn when an image is available and branches the tool-result build on `ToolImage`. The shell adds JPEG downscale/encode helpers and attaches the image to every diff-gated `OcrMsg`. All fail-soft to text-only.

**Tech Stack:** Python 3.11 / pydantic / pytest (async, `asyncio_mode=auto`) / the `anthropic` SDK (multimodal — no new dep); Swift 5.9 AppKit (CoreGraphics JPEG encode).

**Spec:** `docs/superpowers/specs/2026-06-15-phase6-vision-design.md`

**Note (deliberate deviation from the spec):** the spec describes a "sparse-OCR heuristic" to decide when to attach the image. This plan instead **always attaches the downscaled image on a changed frame** (`processFrame` only runs after the diff gate, so this is "on change", not "every frame"). This is simpler and avoids false-negatives on text-heavy screenshots; the `look_at_conversation` tool remains the real cost gate (vision tokens are spent only when the agent looks). The shell heuristic test in the spec is therefore replaced by the `ImageUtil` encode test.

---

## File Structure

**Brain — create:** `brain/src/glimpse_brain/vision_tool.py` (`LookTool`); tests `test_vision_tool.py`.
**Brain — modify:** `tooluse.py` (`ToolImage`, `ToolResult` image fields, image block), `tools.py` (`Tool.run -> str | ToolImage`), `agent.py` (`suggest(image=)`, per-turn `LookTool`, loop branch), `protocol.py` (`OcrMsg.image`), `server.py` (`_last_image`); tests `test_tooluse.py`, `test_agent.py`, `test_protocol.py`, `test_server.py`.
**Shell — modify:** `ImageUtil.swift` (downscale+JPEG+base64), `Protocol.swift` (`OcrMsg.image`), `main.swift` (`processFrame` attaches image); test `ImageUtilTests.swift`, `ProtocolTests.swift`.

Run brain from `brain/`: `./.venv/bin/python -m pytest ...`; shell from `shell/`: `swift test` / `swift build`.

---

## Task 1: Image tool-results in the neutral seam (`tooluse.py`)

**Files:** Modify `brain/src/glimpse_brain/tooluse.py`. Test: `brain/tests/test_tooluse.py`.

- [ ] **Step 1: Write the failing test** — add to `brain/tests/test_tooluse.py`:

```python
def test_image_tool_result_emits_anthropic_image_block() -> None:
    # WHY: a look-at-image tool returns an image; the seam must render it as a
    # valid Anthropic image block in the tool_result content.
    from glimpse_brain.tooluse import (
        AgentStep, ToolCall, ToolResult, ToolResultsMessage, UserMessage,
        _to_anthropic_messages,
    )
    transcript = [
        UserMessage(text="客户: 这个怎么样"),
        AgentStep(tool_calls=(ToolCall(id="t1", name="look_at_conversation", input={}),)),
        ToolResultsMessage(results=(
            ToolResult(id="t1", image_b64="QUJD", media_type="image/jpeg"),
        )),
    ]
    msgs = _to_anthropic_messages(transcript)
    block = msgs[2]["content"][0]
    assert block["type"] == "tool_result" and block["tool_use_id"] == "t1"
    img = block["content"][0]
    assert img["type"] == "image"
    assert img["source"] == {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}


def test_text_tool_result_unchanged() -> None:
    from glimpse_brain.tooluse import ToolResult, ToolResultsMessage, _to_anthropic_messages
    msgs = _to_anthropic_messages([ToolResultsMessage(results=(ToolResult(id="t1", output="政策"),))])
    assert msgs[0]["content"][0] == {"type": "tool_result", "tool_use_id": "t1", "content": "政策"}
```

- [ ] **Step 2: Run to verify it fails** — `cd brain && ./.venv/bin/python -m pytest tests/test_tooluse.py -k tool_result -v` → FAIL (`ToolResult` has no `image_b64`).

- [ ] **Step 3: Implement** — in `brain/src/glimpse_brain/tooluse.py`:

(a) Add `output` default + image fields to `ToolResult`, and add `ToolImage` (place near `ToolResult`):
```python
@dataclass(frozen=True)
class ToolResult:
    id: str          # matches a ToolCall.id
    output: str = ""
    image_b64: str = ""
    media_type: str = ""


@dataclass(frozen=True)
class ToolImage:
    """An image a tool returns (the agent sees it as an Anthropic image block)."""
    image_b64: str
    media_type: str = "image/jpeg"
```

(b) In `_to_anthropic_messages`, replace the `ToolResultsMessage` branch's inline block with a helper. Change:
```python
        elif isinstance(entry, ToolResultsMessage):
            messages.append({
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": r.id, "content": r.output}
                    for r in entry.results
                ],
            })
```
to:
```python
        elif isinstance(entry, ToolResultsMessage):
            messages.append({
                "role": "user",
                "content": [_tool_result_block(r) for r in entry.results],
            })
```
and add this module-level helper (after `_to_anthropic_messages`):
```python
def _tool_result_block(result: ToolResult) -> dict[str, Any]:
    if result.image_b64:
        return {
            "type": "tool_result",
            "tool_use_id": result.id,
            "content": [{
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": result.media_type or "image/jpeg",
                    "data": result.image_b64,
                },
            }],
        }
    return {"type": "tool_result", "tool_use_id": result.id, "content": result.output}
```

- [ ] **Step 4: Run to verify it passes** — `cd brain && ./.venv/bin/python -m pytest tests/test_tooluse.py -v` → PASS. Ruff: `./.venv/bin/ruff check src/glimpse_brain/tooluse.py tests/test_tooluse.py` → clean.

- [ ] **Step 5: Commit**

```bash
git add brain/src/glimpse_brain/tooluse.py brain/tests/test_tooluse.py
git commit -m "feat(brain): image tool-results in the provider-neutral seam"
```

---

## Task 2: `LookTool` (`vision_tool.py`)

**Files:** Create `brain/src/glimpse_brain/vision_tool.py`. Test: `brain/tests/test_vision_tool.py`.

- [ ] **Step 1: Write the failing test** — create `brain/tests/test_vision_tool.py`:

```python
from __future__ import annotations

from glimpse_brain.tooluse import ToolImage
from glimpse_brain.vision_tool import LookTool


async def test_look_returns_held_image() -> None:
    tool = LookTool(image_b64="QUJD", media_type="image/jpeg")
    out = await tool.run({})
    assert isinstance(out, ToolImage)
    assert out.image_b64 == "QUJD" and out.media_type == "image/jpeg"
    assert tool.name == "look_at_conversation"
    assert tool.input_schema["required"] == []


async def test_look_with_no_image_is_friendly_text() -> None:
    # WHY: if the held image was cleared, looking must degrade to text, not crash.
    tool = LookTool(image_b64="")
    out = await tool.run({})
    assert isinstance(out, str) and "暂无" in out
```

- [ ] **Step 2: Run to verify it fails** — `cd brain && ./.venv/bin/python -m pytest tests/test_vision_tool.py -v` → FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement** — create `brain/src/glimpse_brain/vision_tool.py`:

```python
"""Vision tool: lets the agent look at the current conversation-region image.
Returns a ToolImage (an Anthropic image block) the agent sees in its next turn."""

from __future__ import annotations

from typing import Any

from glimpse_brain.tooluse import ToolImage


class LookTool:
    name = "look_at_conversation"
    description = "查看当前对话区域的截图。客户可能发来商品图片/截图时调用，看清后再回复。"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    def __init__(self, image_b64: str, media_type: str = "image/jpeg") -> None:
        self._image_b64 = image_b64
        self._media_type = media_type

    async def run(self, input: dict[str, Any]) -> str | ToolImage:
        if not self._image_b64:
            return "（暂无可查看的图片）"
        return ToolImage(image_b64=self._image_b64, media_type=self._media_type)
```

- [ ] **Step 4: Run to verify it passes** — `cd brain && ./.venv/bin/python -m pytest tests/test_vision_tool.py -v` → PASS (2). Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add brain/src/glimpse_brain/vision_tool.py brain/tests/test_vision_tool.py
git commit -m "feat(brain): LookTool — agent looks at the conversation image"
```

---

## Task 3: Agent vision integration (`agent.py`, `tools.py`)

**Files:** Modify `brain/src/glimpse_brain/agent.py`, `brain/src/glimpse_brain/tools.py`. Test: `brain/tests/test_agent.py`.

- [ ] **Step 1: Write the failing test** — append to `brain/tests/test_agent.py`:

```python
async def test_agent_looks_at_image_when_available() -> None:
    # WHY: with an image available, the agent can call look_at_conversation, see
    # it (image tool-result fed back), and finalize a grounded reply.
    from glimpse_brain.tooluse import AgentStep, ToolCall, ToolImage

    fed_back: dict = {}

    class LookThenFinalize:
        def __init__(self) -> None:
            self.turns = 0

        async def run_turn(self, *, system, transcript, tools) -> AgentStep:
            self.turns += 1
            if self.turns == 1:
                return AgentStep(tool_calls=(ToolCall(id="l1", name="look_at_conversation", input={}),))
            fed_back["last"] = transcript[-1]  # the ToolResultsMessage we built
            return AgentStep(final_text='["看到了，这款是经典款黑色"]')

    agent = Agent(
        client=LookThenFinalize(), system="SYS", knowledge=FakeKB(),
        redactor=Redactor([]), limiter=RateLimiter(10),
        max_suggestions=3, max_iterations=4,
    )
    result = await agent.suggest(["客户: 这个还有货吗"], image="QUJD")
    assert result.drafts == ["看到了，这款是经典款黑色"]
    assert "look_at_conversation" in result.tools_used
    # the fed-back tool result carried the image, not text
    img_result = fed_back["last"].results[0]
    assert img_result.image_b64 == "QUJD"


async def test_agent_omits_look_tool_without_image() -> None:
    from glimpse_brain.tooluse import AgentStep

    captured = {}

    class CaptureTools:
        async def run_turn(self, *, system, transcript, tools) -> AgentStep:
            captured["names"] = [t.name for t in tools]
            return AgentStep(final_text='["在的"]')

    agent = Agent(
        client=CaptureTools(), system="SYS", knowledge=FakeKB(),
        redactor=Redactor([]), limiter=RateLimiter(10),
        max_suggestions=3, max_iterations=4,
    )
    await agent.suggest(["客户: 在吗"], image=None)
    assert "look_at_conversation" not in captured["names"]
```

- [ ] **Step 2: Run to verify it fails** — `cd brain && ./.venv/bin/python -m pytest tests/test_agent.py -k look -v` → FAIL (`suggest` has no `image`).

- [ ] **Step 3: Implement**

(a) `tools.py` — widen `Tool.run`'s return type. Change the `Tool` Protocol's `run` signature:
```python
    async def run(self, input: dict[str, Any]) -> "str | ToolImage": ...
```
and add the import at the top of `tools.py`:
```python
from glimpse_brain.tooluse import ToolImage
```
(`KnowledgeBaseTool.run` still returns `str` — that satisfies `str | ToolImage`.)

(b) `agent.py` — add the import:
```python
from glimpse_brain.vision_tool import LookTool
from glimpse_brain.tooluse import ToolImage
```
(Add `ToolImage` to the existing `from glimpse_brain.tooluse import (...)` block rather than a second import line.)

(c) `agent.py` — `suggest` gains `image`, registers `LookTool` when present, and the loop branches on `ToolImage`. Replace the `suggest` signature and the tool-result-building part of the loop. New signature line:
```python
    async def suggest(
        self, tail: list[str], customer: str | None = None, image: str | None = None
    ) -> AgentResult:
```
After the memory-tools block (`if self._memory is not None and customer: ...`), add:
```python
        if image:
            tools.append(LookTool(image))
```
And in the loop, replace the tool-result append:
```python
                results.append(ToolResult(id=call.id, output=output))
```
with:
```python
                if isinstance(output, ToolImage):
                    results.append(ToolResult(
                        id=call.id, image_b64=output.image_b64, media_type=output.media_type
                    ))
                else:
                    results.append(ToolResult(id=call.id, output=output))
```

- [ ] **Step 4: Run to verify it passes** — `cd brain && ./.venv/bin/python -m pytest tests/test_agent.py -v` → PASS (all — existing tests call `suggest(tail)` / `suggest(tail, customer=...)`, `image` defaults None → no LookTool). Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add brain/src/glimpse_brain/agent.py brain/src/glimpse_brain/tools.py brain/tests/test_agent.py
git commit -m "feat(brain): agent looks at the conversation image when available"
```

---

## Task 4: `OcrMsg.image` wire field

**Files:** Modify `brain/src/glimpse_brain/protocol.py`, `shell/Sources/GlimpseShellLib/Protocol.swift`. Test: `brain/tests/test_protocol.py`, `shell/Tests/GlimpseShellTests/ProtocolTests.swift`.

- [ ] **Step 1: Write the failing tests**

Brain — add to `brain/tests/test_protocol.py`:
```python
def test_ocr_image_defaults_empty_and_roundtrips() -> None:
    line = '{"type":"ocr","seq":1,"ts":"t","region_id":"r","blocks":[]}'
    parsed = parse_inbound(line)
    assert isinstance(parsed, OcrMsg) and parsed.image == ""
    msg = OcrMsg(seq=2, ts="t", region_id="r", blocks=[], image="QUJD")
    assert parse_inbound(to_line(msg)).image == "QUJD"
```

Swift — add to `shell/Tests/GlimpseShellTests/ProtocolTests.swift`:
```swift
@Test
func ocrMsgEncodesImage() throws {
    let data = try Wire.encodeLine(
        OcrMsg(seq: 1, ts: "t", regionId: "r", blocks: [], image: "QUJD")
    )
    let line = String(data: data, encoding: .utf8)!
    #expect(line.contains("\"image\":\"QUJD\""))
}
```

- [ ] **Step 2: Run to verify they fail** — brain FAIL (no `image`); swift FAIL (compile).

- [ ] **Step 3: Implement**

Brain — in `protocol.py`, add to `OcrMsg` after `contact`:
```python
    image: str = ""  # optional base64 JPEG of the conversation region; "" = none
```

Swift — in `Protocol.swift`, add `image` to `OcrMsg` (stored var, defaulted init param after `contact`, and a `CodingKeys` case). Update the struct so the init is:
```swift
    public init(seq: Int, ts: String, regionId: String, blocks: [Block], contact: String = "", image: String = "") {
        self.seq = seq
        self.ts = ts
        self.regionId = regionId
        self.blocks = blocks
        self.contact = contact
        self.image = image
    }
```
add `public var image: String` alongside `contact`, and add `image` to the `CodingKeys` enum (`case blocks, contact, image`).

- [ ] **Step 4: Run to verify they pass** — brain `pytest tests/test_protocol.py -v` PASS; swift `swift test --filter ProtocolTests` PASS + `swift build` clean (existing `OcrMsg(...)` callers compile via the defaulted param).

- [ ] **Step 5: Commit**

```bash
git add brain/src/glimpse_brain/protocol.py shell/Sources/GlimpseShellLib/Protocol.swift brain/tests/test_protocol.py shell/Tests/GlimpseShellTests/ProtocolTests.swift
git commit -m "feat(protocol): optional OcrMsg.image (downscaled conversation JPEG)"
```

---

## Task 5: Server holds the latest image + passes it to the agent

**Files:** Modify `brain/src/glimpse_brain/server.py`. Test: `brain/tests/test_server.py`.

- [ ] **Step 1: Write the failing test** — add to `brain/tests/test_server.py`:

```python
async def test_server_offers_look_tool_when_image_present(tmp_path: Path) -> None:
    # WHY: an OcrMsg carrying an image makes the look tool available to the agent.
    cfg = make_config(tmp_path)

    class ToolSnoop:
        def __init__(self) -> None:
            self.saw_look = False

        async def run_turn(self, *, system: str, transcript: list, tools: list) -> AgentStep:
            self.saw_look = any(t.name == "look_at_conversation" for t in tools)
            return AgentStep(final_text='["好的"]')

    client = ToolSnoop()
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=client)
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(b'{"type":"hello","shell_version":"0.1.0"}\n')
        await writer.drain()
        await read_until(reader, "status")
        line = ('{"type":"ocr","seq":1,"ts":"t","region_id":"region-1","image":"QUJD",'
                '"blocks":[{"text":"这个还有吗","x0":0.05,"x1":0.4,"conf":0.95}]}\n')
        writer.write(line.encode())
        await writer.drain()
        await read_until(reader, "suggestions")
        assert client.saw_look
        writer.close()
    finally:
        task.cancel()
```
(`make_config` already sets `memory.enabled=False`, so no MemPalace; this test only exercises the image path.)

- [ ] **Step 2: Run to verify it fails** — FAIL (`saw_look` False: no image wiring).

- [ ] **Step 3: Implement** — in `brain/src/glimpse_brain/server.py`:

(a) In `__init__`, add `self._last_image: str = ""` (near `self._current_customer`).

(b) In `_on_ocr`, after setting `self._current_customer`, add:
```python
        if msg.image:
            self._last_image = msg.image
```

(c) In `_fire`, pass the image to the agent — change the `suggest` call to:
```python
            result = await self._agent.suggest(
                self._tracker.tail(),
                customer=self._current_customer,
                image=self._last_image or None,
            )
```

- [ ] **Step 4: Run to verify** — `cd brain && ./.venv/bin/python -m pytest -q` → all pass. Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add brain/src/glimpse_brain/server.py brain/tests/test_server.py
git commit -m "feat(brain): server holds latest image, passes it to the agent"
```

---

## Task 6: Shell `ImageUtil` — downscale + JPEG + base64

**Files:** Modify `shell/Sources/GlimpseShellLib/ImageUtil.swift`. Test: `shell/Tests/GlimpseShellTests/ImageUtilTests.swift`.

- [ ] **Step 1: Write the failing test** — create `shell/Tests/GlimpseShellTests/ImageUtilTests.swift`:

```swift
import CoreGraphics
import Foundation
import Testing
@testable import GlimpseShellLib

private func solidImage(width: Int, height: Int) -> CGImage {
    let cs = CGColorSpaceCreateDeviceRGB()
    let ctx = CGContext(
        data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
        space: cs, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    )!
    ctx.setFillColor(CGColor(red: 0.2, green: 0.4, blue: 0.6, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
    return ctx.makeImage()!
}

@Test
func downscaledJPEGBase64ProducesDecodableImageUnderCap() throws {
    let big = solidImage(width: 3000, height: 2000)
    let b64 = ImageUtil.downscaledJPEGBase64(big, maxDimension: 1024, quality: 0.6)
    let b64v = try #require(b64)
    #expect(!b64v.isEmpty)
    let data = try #require(Data(base64Encoded: b64v))
    let src = try #require(CGImageSourceCreateWithData(data as CFData, nil))
    let decoded = try #require(CGImageSourceCreateImageAtIndex(src, 0, nil))
    // longest side downscaled to the cap (3000 → 1024)
    #expect(max(decoded.width, decoded.height) <= 1024)
}
```

- [ ] **Step 2: Run to verify it fails** — `cd shell && swift test --filter ImageUtil 2>&1 | tail -5` → FAIL (no `downscaledJPEGBase64`).

- [ ] **Step 3: Implement** — add to `shell/Sources/GlimpseShellLib/ImageUtil.swift` (it already has `cgImage(from:)`; add):

```swift
    /// Downscale so the longest side is <= maxDimension, JPEG-encode, base64.
    /// Keeps the conversation-region screenshot small enough for the socket + Claude.
    public static func downscaledJPEGBase64(
        _ image: CGImage, maxDimension: CGFloat = 1024, quality: CGFloat = 0.6
    ) -> String? {
        let w = CGFloat(image.width), h = CGFloat(image.height)
        let scale = min(1, maxDimension / max(w, h))
        let outW = Int((w * scale).rounded()), outH = Int((h * scale).rounded())
        guard outW > 0, outH > 0,
            let ctx = CGContext(
                data: nil, width: outW, height: outH, bitsPerComponent: 8, bytesPerRow: 0,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            )
        else { return nil }
        ctx.interpolationQuality = .medium
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: outW, height: outH))
        guard let scaled = ctx.makeImage() else { return nil }

        let data = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(
            data as CFMutableData, "public.jpeg" as CFString, 1, nil
        ) else { return nil }
        CGImageDestinationAddImage(dest, scaled, [
            kCGImageDestinationLossyCompressionQuality: quality
        ] as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { return nil }
        return (data as Data).base64EncodedString()
    }
```
Add `import ImageIO` and `import AppKit` (for `CGImageDestination*`) at the top of `ImageUtil.swift` if not already present (read the file first; it likely imports `CoreVideo`/`CoreGraphics` — add `ImageIO`).

- [ ] **Step 4: Run to verify** — `cd shell && swift test --filter ImageUtil 2>&1 | tail -5` → PASS; `swift build 2>&1 | tail -3` → builds.

- [ ] **Step 5: Commit**

```bash
git add shell/Sources/GlimpseShellLib/ImageUtil.swift shell/Tests/GlimpseShellTests/ImageUtilTests.swift
git commit -m "feat(shell): ImageUtil downscale + JPEG + base64"
```

---

## Task 7: Shell `processFrame` attaches the image

**Files:** Modify `shell/Sources/GlimpseShell/main.swift`.

UI/capture wiring — verified by compile + existing tests staying green (no new unit test).

- [ ] **Step 1: Wire the image into the OcrMsg.** In `processFrame` (after `let blocks = (try? OCR.recognize(image)) ?? []` and before/at the `OcrMsg(...)` construction at ~line 276), encode the already-captured `image` (a `CGImage`) and pass it. The `OcrMsg(...)` call currently passes `seq/ts/regionId/blocks/contact`; add `image:`:
```swift
        let imageB64 = ImageUtil.downscaledJPEGBase64(image) ?? ""
        let message = OcrMsg(
            seq: seq, ts: isoFormatter.string(from: Date()),
            regionId: regionId, blocks: blocks, contact: contactReader.current,
            image: imageB64
        )
```
(`image` is the `CGImage` already produced at `ImageUtil.cgImage(from: pixelBuffer)` earlier in `processFrame`. `processFrame` only runs on diff-gated changed frames, so this attaches the downscaled image on change — the `look_at_conversation` tool is the cost gate for actually sending it to Claude.)

- [ ] **Step 2: Build + test** — `cd shell && swift build 2>&1 | tail -3` → builds; `swift test 2>&1 | tail -3` → all pass. Report any warnings.

- [ ] **Step 3: Commit**

```bash
git add shell/Sources/GlimpseShell/main.swift
git commit -m "feat(shell): attach downscaled conversation image to OcrMsg"
```

---

## Task 8: Full suite + docs + manual E2E

**Files:** Modify `README.md`.

- [ ] **Step 1: Run everything** — `cd brain && ./.venv/bin/python -m pytest -q` (all pass, integration deselected) + `./.venv/bin/ruff check src/ tests/` (clean); `cd shell && swift test 2>&1 | tail -3` (all pass). Report counts. If anything fails, STOP and report BLOCKED.

- [ ] **Step 2: Add a README section** (match the existing Phase headings):

```markdown
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
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: Phase 6 the agent can see (vision usage + E2E)"
```

---

## Self-Review

**Spec coverage:**
- Shell sends downscaled conversation JPEG as `OcrMsg.image` → Tasks 4, 6, 7. ✓ (always-on-change instead of the sparse heuristic — deviation noted in the header.)
- Brain holds latest image; `look_at_conversation` returns it; offered only when available → Tasks 2, 3, 5. ✓
- Extend neutral seam for image tool-results → Task 1. ✓
- Agent decides when to look (cost gate) → Task 3 (LookTool only registered when image present; agent chooses to call it). ✓
- Fail-soft (no image → text-only; look with no image → friendly text) → Tasks 2, 3, 5. ✓
- `OcrMsg.image` optional/back-compat → Task 4. ✓
- Testing (seam, LookTool, agent vision path, protocol, server, ImageUtil) → Tasks 1–6; manual E2E → Task 8. ✓
- Vision = Claude, no new dep → no dependency task; the image flows through the existing anthropic tool-use loop. ✓

**Placeholder scan:** No TBD/TODO; every code step has complete code. The Task 7 shell wiring references the existing `image` CGImage + `contactReader.current` + `OcrMsg(... image:)` (all defined in prior tasks/phases).

**Type consistency:** `ToolImage(image_b64, media_type)` + `ToolResult(id, output, image_b64, media_type)` (Task 1) used identically in Tasks 2, 3. `LookTool(image_b64, media_type)` + `run -> str | ToolImage` (Task 2) match `Tool.run` widening (Task 3) and agent usage (Task 3). `Agent.suggest(tail, customer=None, image=None)` (Task 3) matches the server call (Task 5). `OcrMsg(..., image="")` (Task 4) matches the shell send (Task 7) and server read (Task 5). `ImageUtil.downscaledJPEGBase64(_:maxDimension:quality:)` (Task 6) matches the `processFrame` call (Task 7).

No gaps found.
