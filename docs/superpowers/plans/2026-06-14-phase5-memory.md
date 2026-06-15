# Phase 5 — Memory Subsystem Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the agent per-customer memory — OCR'd contact-name identity keys a MemPalace-backed dossier of deterministic interaction capture + agent-distilled facts, recalled/written via agent tools, all fail-soft.

**Architecture:** A thin async `Memory` Protocol with an `InMemoryMemory` fake (for fast unit tests) and a `MemPalaceMemory` impl (library, version-pinned, embeddinggemma). The P4 `Agent` gains per-turn `recall_customer`/`remember_about_customer` tools bound to the current customer. The shell calibrates a contact-name region, OCRs it each cycle, and sends it as `OcrMsg.contact`; the brain scopes memory to that customer and auto-captures interactions. Every memory failure degrades to KB-only drafting.

**Tech Stack:** Python 3.11 / pydantic / pytest (async, `asyncio_mode=auto`) / `mempalace` (pinned) / Swift 5.9 AppKit (shell). Memory ops are async; the sync MemPalace calls run via `asyncio.to_thread`.

**Spec:** `docs/superpowers/specs/2026-06-14-phase5-memory-design.md`

---

## File Structure

**Brain — create:**
- `brain/src/glimpse_brain/memory.py` — `MemoryHit`, `Memory` Protocol, `InMemoryMemory` fake.
- `brain/src/glimpse_brain/mempalace_memory.py` — `MemPalaceMemory` (the spike).
- `brain/src/glimpse_brain/memory_tools.py` — `RecallCustomerTool`, `RememberAboutCustomerTool`.
- Tests: `brain/tests/test_memory.py`, `test_memory_tools.py`, `test_mempalace_memory.py` (opt-in integration).

**Brain — modify:**
- `brain/src/glimpse_brain/agent.py` — `memory`/`recall_k` deps; per-turn memory tools; `suggest(tail, customer=None)`.
- `brain/src/glimpse_brain/protocol.py` + `shell/.../Protocol.swift` — `OcrMsg.contact`.
- `brain/src/glimpse_brain/config.py` — `MemoryCfg`.
- `brain/src/glimpse_brain/server.py` — current-customer tracking, deterministic capture, memory construction, AGENT_SYSTEM tools note.
- Tests: `test_agent.py`, `test_protocol.py`, `test_server.py`, `test_config.py`.

**Shell — create/modify:**
- `shell/Sources/GlimpseShellLib/ContactRegionStore.swift` (new) + test.
- `shell/Sources/GlimpseShellLib/ContactReader.swift` (new) — capture+OCR the contact region.
- `shell/Sources/GlimpseShellLib/Overlay.swift`, `shell/Sources/GlimpseShell/main.swift` — calibration menu, wiring, name display.

Run brain from `brain/`: `./.venv/bin/python -m pytest ...`; shell from `shell/`: `swift test`.

---

## Task 1: `Memory` interface + in-memory fake

**Files:**
- Create: `brain/src/glimpse_brain/memory.py`
- Test: `brain/tests/test_memory.py`

- [ ] **Step 1: Write the failing test** — create `brain/tests/test_memory.py`:

```python
from __future__ import annotations

from glimpse_brain.memory import InMemoryMemory, MemoryHit


async def test_write_then_recall_returns_content() -> None:
    mem = InMemoryMemory()
    await mem.write("小明", "曾因 SKU-A 破损退货", "fact")
    hits = await mem.recall("小明", "破损", k=5)
    assert any(isinstance(h, MemoryHit) and "破损" in h.text for h in hits)


async def test_recall_is_scoped_per_customer() -> None:
    # WHY: the dossier boundary is the whole point — one customer's memory must
    # never surface for another.
    mem = InMemoryMemory()
    await mem.write("小明", "偏好顺丰", "fact")
    assert await mem.recall("小红", "顺丰", k=5) == []


async def test_recall_respects_k_and_returns_recent_first() -> None:
    mem = InMemoryMemory()
    for i in range(5):
        await mem.write("小明", f"互动{i}", "interaction")
    hits = await mem.recall("小明", "互动", k=2)
    assert len(hits) == 2
    assert hits[0].text == "互动4"  # most recent first
```

- [ ] **Step 2: Run to verify it fails** — `cd brain && ./.venv/bin/python -m pytest tests/test_memory.py -v` → FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement** — create `brain/src/glimpse_brain/memory.py`:

```python
"""Per-customer memory: a thin async seam with an in-memory fake for tests.
MemPalaceMemory (mempalace_memory.py) is the production impl behind this."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MemoryHit:
    text: str
    kind: str          # "interaction" | "fact"
    score: float = 0.0


class Memory(Protocol):
    async def recall(self, customer: str, query: str, k: int) -> list[MemoryHit]: ...
    async def write(self, customer: str, content: str, kind: str) -> None: ...


class InMemoryMemory:
    """Test double + reference semantics. Recall = substring match (or all when
    the query matches nothing), most-recent-first, capped at k."""

    def __init__(self) -> None:
        self._store: dict[str, list[MemoryHit]] = {}

    async def recall(self, customer: str, query: str, k: int) -> list[MemoryHit]:
        hits = self._store.get(customer, [])
        matched = [h for h in hits if query and query in h.text]
        ordered = list(reversed(matched if matched else hits))
        return ordered[:k]

    async def write(self, customer: str, content: str, kind: str) -> None:
        self._store.setdefault(customer, []).append(MemoryHit(text=content, kind=kind))
```

- [ ] **Step 4: Run to verify it passes** — `cd brain && ./.venv/bin/python -m pytest tests/test_memory.py -v` → PASS (3). Ruff: `./.venv/bin/ruff check src/glimpse_brain/memory.py tests/test_memory.py` → clean.

- [ ] **Step 5: Commit**

```bash
git add brain/src/glimpse_brain/memory.py brain/tests/test_memory.py
git commit -m "feat(brain): Memory interface + in-memory fake"
```

---

## Task 2: `MemPalaceMemory` — write-path spike (de-risk first)

**Files:**
- Modify: `brain/pyproject.toml` (add pinned `mempalace` dependency)
- Create: `brain/src/glimpse_brain/mempalace_memory.py`
- Test: `brain/tests/test_mempalace_memory.py` (opt-in integration)

This task is a **spike**: the read path (`searcher.search_memories`) is verified; the
**write path is not**. Try the candidates below cleanest-first; the acceptance
criterion is the integration test (write a fact, recall it back scoped to its wing).

- [ ] **Step 1: Add the dependency** — in `brain/pyproject.toml`, add to `dependencies`:
```toml
    "mempalace==3.4.0",
```
Then `cd brain && ./.venv/bin/pip install -e .` (or `pip install mempalace==3.4.0`). Confirm `./.venv/bin/python -c "import mempalace; print(mempalace.__version__)"` prints `3.4.0`.

- [ ] **Step 2: Write the opt-in integration test** — create `brain/tests/test_mempalace_memory.py`:

```python
"""Opt-in integration test for MemPalaceMemory against a real temp palace.
Slow (downloads the embedding model) → excluded from the default pytest run.
Run explicitly: ./.venv/bin/python -m pytest tests/test_mempalace_memory.py -m integration -v"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_write_then_recall_roundtrips_scoped_to_wing(tmp_path) -> None:
    from glimpse_brain.mempalace_memory import MemPalaceMemory

    mem = MemPalaceMemory(palace_path=tmp_path / "palace", embedding_model="minilm")
    await mem.write("测试客户", "曾因 SKU-A 破损退货", "fact")
    hits = await mem.recall("测试客户", "破损退货", k=5)
    assert any("破损" in h.text for h in hits)
    # scoped: a different wing sees nothing
    assert await mem.recall("其他客户", "破损退货", k=5) == []
```

Register the `integration` marker: add to `brain/pyproject.toml` under `[tool.pytest.ini_options]` (create the table if absent):
```toml
markers = ["integration: slow tests needing the real mempalace model (opt-in)"]
addopts = "-m 'not integration'"
```
(`addopts` excludes integration from the default run, matching the eval-suite discipline.)

- [ ] **Step 3: Run to verify it fails** — `cd brain && ./.venv/bin/python -m pytest tests/test_mempalace_memory.py -m integration -v` → FAIL (ModuleNotFoundError on `mempalace_memory`).

- [ ] **Step 4: Implement** — create `brain/src/glimpse_brain/mempalace_memory.py`. Recall is known; **spike the write**.

```python
"""Production Memory over MemPalace (pinned 3.4.0). Read via searcher.search_memories
(verified). Write: see the spike note below. All blocking mempalace calls run in a
thread so the asyncio event loop is never blocked."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from glimpse_brain.memory import MemoryHit


def _safe_wing(customer: str) -> str:
    # MemPalace wing-naming: strip path separators / leading-trailing junk.
    return re.sub(r"[\\/]+", "_", customer).strip(" ._-") or "unknown"


class MemPalaceMemory:
    def __init__(self, palace_path: Path, embedding_model: str = "embeddinggemma") -> None:
        self._palace = str(palace_path)
        self._embedding_model = embedding_model
        Path(palace_path).mkdir(parents=True, exist_ok=True)

    async def recall(self, customer: str, query: str, k: int) -> list[MemoryHit]:
        return await asyncio.to_thread(self._recall_sync, customer, query, k)

    def _recall_sync(self, customer: str, query: str, k: int) -> list[MemoryHit]:
        from mempalace import searcher

        result = searcher.search_memories(
            query, palace_path=self._palace, wing=_safe_wing(customer), n_results=k
        )
        rows = result.get("results", []) if isinstance(result, dict) else []
        return [
            MemoryHit(
                text=row.get("text", ""),
                kind=str(row.get("room", "")),
                score=float(row.get("similarity", 0.0)),
            )
            for row in rows
        ]

    async def write(self, customer: str, content: str, kind: str) -> None:
        await asyncio.to_thread(self._write_sync, customer, content, kind)

    def _write_sync(self, customer: str, content: str, kind: str) -> None:
        # === SPIKE: pick the cleanest working write path ===
        # Candidate A (preferred): a mempalace BaseSourceAdapter (RFC-002).
        # Candidate B: lower-level — mempalace.palace.get_collection(...) for the
        #   drawers collection, then collection.add(documents=[content],
        #   metadatas=[{"wing": _safe_wing(customer), "room": kind, "kind": kind}],
        #   ids=[a uuid]). Inspect how mempalace.miner writes drawers to match the
        #   exact collection name + metadata keys that search_memories filters on
        #   (wing/room). Use mempalace's embedding function for the palace.
        # Candidate C: write `content` to a temp .md file under a per-wing dir and
        #   call mempalace.miner.mine(...) in-process.
        # Replace this body with whichever candidate makes the integration test
        # (test_mempalace_memory.py) pass. If NONE is clean, STOP and report —
        # that is the day-one signal to reconsider (port), per the spec.
        raise NotImplementedError("spike: implement the write path")
```

**Spike acceptance:** replace `_write_sync` so `pytest tests/test_mempalace_memory.py -m integration` passes. Configure the palace's embedder to `embeddinggemma-300m` for production (the test uses `minilm` for speed — wire `embedding_model` through to whatever mempalace API selects the model; inspect `mempalace.embedding.get_embedding_function` / onboarding). If wiring the model per-palace is non-trivial, document what you found and default to mempalace's configured model, leaving `embedding_model` as a recorded intent.

- [ ] **Step 5: Run to verify it passes** — `cd brain && ./.venv/bin/python -m pytest tests/test_mempalace_memory.py -m integration -v` → PASS. Confirm the default run still excludes it: `./.venv/bin/python -m pytest -q` (integration NOT collected). Ruff clean on the new file.

- [ ] **Step 6: Commit**

```bash
git add brain/pyproject.toml brain/src/glimpse_brain/mempalace_memory.py brain/tests/test_mempalace_memory.py
git commit -m "feat(brain): MemPalaceMemory (recall + spiked write path) behind Memory"
```

**If the spike reveals the write path is unworkable:** report DONE_WITH_CONCERNS with findings and stop — the controller decides whether to port (per the spec's reserved fallback) before continuing.

---

## Task 3: Memory tools (`recall_customer` / `remember_about_customer`)

**Files:**
- Create: `brain/src/glimpse_brain/memory_tools.py`
- Test: `brain/tests/test_memory_tools.py`

- [ ] **Step 1: Write the failing test** — create `brain/tests/test_memory_tools.py`:

```python
from __future__ import annotations

from glimpse_brain.memory import InMemoryMemory
from glimpse_brain.memory_tools import RecallCustomerTool, RememberAboutCustomerTool


async def test_recall_tool_scopes_to_bound_customer() -> None:
    mem = InMemoryMemory()
    await mem.write("小明", "偏好顺丰", "fact")
    tool = RecallCustomerTool(mem, customer="小明", k=5)
    out = await tool.run({"query": "顺丰"})
    assert "顺丰" in out
    # the agent never passes the customer — it's bound at construction
    assert tool.name == "recall_customer"
    assert tool.input_schema["required"] == []


async def test_recall_tool_empty_is_friendly() -> None:
    tool = RecallCustomerTool(InMemoryMemory(), customer="新客户", k=5)
    out = await tool.run({"query": "x"})
    assert "暂无" in out


async def test_remember_tool_writes_a_fact() -> None:
    mem = InMemoryMemory()
    tool = RememberAboutCustomerTool(mem, customer="小明")
    out = await tool.run({"fact": "对包装要求高"})
    assert "已记住" in out
    hits = await mem.recall("小明", "包装", k=5)
    assert any(h.kind == "fact" and "包装" in h.text for h in hits)


async def test_remember_tool_ignores_empty_fact() -> None:
    mem = InMemoryMemory()
    tool = RememberAboutCustomerTool(mem, customer="小明")
    out = await tool.run({})
    assert await mem.recall("小明", "", k=5) == []
    assert "未提供" in out
```

- [ ] **Step 2: Run to verify it fails** — `cd brain && ./.venv/bin/python -m pytest tests/test_memory_tools.py -v` → FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement** — create `brain/src/glimpse_brain/memory_tools.py`:

```python
"""Agent tools for per-customer memory. Each is bound to ONE customer at
construction (the brain injects the current customer), so the model never has to
know or pass the key — it just recalls/remembers 'about the current customer'."""

from __future__ import annotations

from typing import Any

from glimpse_brain.memory import Memory


class RecallCustomerTool:
    name = "recall_customer"
    description = "回忆当前客户的历史互动和已知信息；起草回复前可调用。"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "要回忆的内容或关键词"}},
        "required": [],
    }

    def __init__(self, memory: Memory, customer: str, k: int) -> None:
        self._memory = memory
        self._customer = customer
        self._k = k

    async def run(self, input: dict[str, Any]) -> str:
        hits = await self._memory.recall(self._customer, input.get("query", ""), self._k)
        if not hits:
            return "（暂无该客户的记忆）"
        return "\n".join(f"- [{h.kind}] {h.text}" for h in hits)


class RememberAboutCustomerTool:
    name = "remember_about_customer"
    description = "记住关于当前客户的一条要点（偏好、历史问题等），供以后回忆。"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"fact": {"type": "string", "description": "要记住的要点"}},
        "required": ["fact"],
    }

    def __init__(self, memory: Memory, customer: str) -> None:
        self._memory = memory
        self._customer = customer

    async def run(self, input: dict[str, Any]) -> str:
        fact = input.get("fact", "").strip()
        if not fact:
            return "（未提供要点）"
        await self._memory.write(self._customer, fact, "fact")
        return f"已记住：{fact}"
```

- [ ] **Step 4: Run to verify it passes** — `cd brain && ./.venv/bin/python -m pytest tests/test_memory_tools.py -v` → PASS (4). Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add brain/src/glimpse_brain/memory_tools.py brain/tests/test_memory_tools.py
git commit -m "feat(brain): customer-bound recall/remember agent tools"
```

---

## Task 4: Agent memory integration

**Files:**
- Modify: `brain/src/glimpse_brain/agent.py`
- Test: `brain/tests/test_agent.py`

- [ ] **Step 1: Write the failing test** — append to `brain/tests/test_agent.py`:

```python
async def test_agent_uses_memory_tools_when_customer_present() -> None:
    # WHY: with a known customer, the agent can recall that customer's memory and
    # ground the draft in it — the core of per-customer memory.
    from glimpse_brain.memory import InMemoryMemory
    from glimpse_brain.tooluse import AgentStep, ToolCall

    mem = InMemoryMemory()
    await mem.write("小明", "偏好顺丰快递", "fact")

    client = ScriptedClient([
        AgentStep(tool_calls=(ToolCall(id="r1", name="recall_customer", input={"query": "快递"}),)),
        AgentStep(final_text='["好的小明，依旧给您发顺丰"]'),
    ])
    agent = Agent(
        client=client, system="SYS", knowledge=FakeKB(),
        redactor=Redactor([]), limiter=RateLimiter(10),
        max_suggestions=3, max_iterations=4, memory=mem, recall_k=5,
    )
    result = await agent.suggest(["客户: 还是老地址发货吧"], customer="小明")
    assert result.drafts == ["好的小明，依旧给您发顺丰"]
    assert "recall_customer" in result.tools_used


async def test_agent_omits_memory_tools_without_customer() -> None:
    # WHY: no identity → fail-soft to KB-only (pure P4); memory tools not offered.
    from glimpse_brain.memory import InMemoryMemory
    from glimpse_brain.tooluse import AgentStep

    captured = {}

    class CaptureToolsClient:
        async def run_turn(self, *, system, transcript, tools) -> AgentStep:
            captured["tool_names"] = [t.name for t in tools]
            return AgentStep(final_text='["在的"]')

    agent = Agent(
        client=CaptureToolsClient(), system="SYS", knowledge=FakeKB(),
        redactor=Redactor([]), limiter=RateLimiter(10),
        max_suggestions=3, max_iterations=4, memory=InMemoryMemory(), recall_k=5,
    )
    await agent.suggest(["客户: 在吗"], customer=None)
    assert captured["tool_names"] == ["knowledge_base"]  # no memory tools
```

- [ ] **Step 2: Run to verify it fails** — `cd brain && ./.venv/bin/python -m pytest tests/test_agent.py -k memory -v` → FAIL (`Agent.__init__` has no `memory`; `suggest` has no `customer`).

- [ ] **Step 3: Implement** — edit `brain/src/glimpse_brain/agent.py`:

(a) Update imports — add at the top with the other imports:
```python
from glimpse_brain.memory import Memory
from glimpse_brain.memory_tools import RecallCustomerTool, RememberAboutCustomerTool
```

(b) Replace `__init__` and `suggest`. Change `__init__` to take `memory`/`recall_k` and keep base tools; rebuild the tool list per turn in `suggest`:
```python
    def __init__(
        self,
        *,
        client: ToolUseClient,
        system: str,
        knowledge: KnowledgeBase,
        redactor: Redactor,
        limiter: RateLimiter,
        max_suggestions: int,
        max_iterations: int,
        memory: Memory | None = None,
        recall_k: int = 5,
    ) -> None:
        self._client = client
        self._system = system
        self._redactor = redactor
        self._limiter = limiter
        self._max = max_suggestions
        self._max_iterations = max_iterations
        self._memory = memory
        self._recall_k = recall_k
        self._base_tools: list[Tool] = [KnowledgeBaseTool(knowledge)]

    async def suggest(self, tail: list[str], customer: str | None = None) -> AgentResult:
        if not self._limiter.allow():
            raise CostCapExceeded("agent turn rate cap reached")
        tools: list[Tool] = list(self._base_tools)
        if self._memory is not None and customer:
            tools.append(RecallCustomerTool(self._memory, customer, self._recall_k))
            tools.append(RememberAboutCustomerTool(self._memory, customer))
        registry = {t.name: t for t in tools}
        conversation = self._redactor.redact("\n".join(tail))
        transcript: list = [
            UserMessage(text=USER_TEMPLATE.format(conversation=conversation, n=self._max))
        ]
        tools_used: list[str] = []
        for _ in range(self._max_iterations):
            step: AgentStep = await self._client.run_turn(
                system=self._system, transcript=transcript, tools=tools
            )
            if step.final_text is not None:
                return AgentResult(
                    drafts=parse_suggestions(step.final_text, self._max),
                    tools_used=tools_used,
                )
            transcript.append(step)
            results = []
            for call in step.tool_calls:
                tools_used.append(call.name)
                tool = registry.get(call.name)
                if tool is None:
                    output = f"unknown tool: {call.name}"
                else:
                    try:
                        output = await tool.run(call.input)
                    except Exception as exc:  # a tool failure must not kill the pass
                        output = f"tool error: {exc}"
                results.append(ToolResult(id=call.id, output=output))
            transcript.append(ToolResultsMessage(results=tuple(results)))
        raise SuggestionParseError("agent did not finalize within max_iterations")
```
(Delete the old `self._tools`/`self._registry` lines and the old loop's use of them — the loop now uses local `tools`/`registry`.)

- [ ] **Step 4: Run to verify it passes** — `cd brain && ./.venv/bin/python -m pytest tests/test_agent.py -v` → PASS (existing P4 tests still pass: `suggest(tail)` defaults `customer=None` → KB-only). Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add brain/src/glimpse_brain/agent.py brain/tests/test_agent.py
git commit -m "feat(brain): agent gains per-turn customer-bound memory tools"
```

---

## Task 5: `OcrMsg.contact` wire field

**Files:**
- Modify: `brain/src/glimpse_brain/protocol.py`, `shell/Sources/GlimpseShellLib/Protocol.swift`
- Test: `brain/tests/test_protocol.py`, `shell/Tests/GlimpseShellTests/ProtocolTests.swift`

- [ ] **Step 1: Write the failing tests**

Brain — add to `brain/tests/test_protocol.py`:
```python
def test_ocr_contact_defaults_empty_and_roundtrips() -> None:
    # WHY: contact is the memory key; it must be optional (old shells omit it)
    # and survive the wire.
    line = '{"type":"ocr","seq":1,"ts":"t","region_id":"r","blocks":[]}'
    parsed = parse_inbound(line)
    assert isinstance(parsed, OcrMsg)
    assert parsed.contact == ""  # back-compatible default

    msg = OcrMsg(seq=2, ts="t", region_id="r", blocks=[], contact="小明")
    assert parse_inbound(to_line(msg)).contact == "小明"
```

Swift — add to `shell/Tests/GlimpseShellTests/ProtocolTests.swift`:
```swift
@Test
func ocrMsgEncodesContact() throws {
    let data = try Wire.encodeLine(
        OcrMsg(seq: 1, ts: "t", regionId: "r", blocks: [], contact: "小明")
    )
    let line = String(data: data, encoding: .utf8)!
    #expect(line.contains("\"contact\":\"小明\""))
}
```

- [ ] **Step 2: Run to verify they fail**
- Brain: `cd brain && ./.venv/bin/python -m pytest tests/test_protocol.py::test_ocr_contact_defaults_empty_and_roundtrips -v` → FAIL.
- Swift: `cd shell && swift test --filter ProtocolTests 2>&1 | tail -5` → FAIL (compile: no `contact` param).

- [ ] **Step 3: Implement**

Brain — in `protocol.py`, add `contact` to `OcrMsg` (after `blocks`):
```python
    contact: str = ""  # OCR'd customer name from the contact-name region; "" = unknown
```

Swift — in `Protocol.swift`, add to `OcrMsg`: a stored `public var contact: String`, default it in the initializer, and add to `CodingKeys`. Update the struct so:
```swift
public struct OcrMsg: Codable {
    public var type = "ocr"
    public var seq: Int
    public var ts: String
    public var regionId: String
    public var blocks: [Block]
    public var contact: String

    public init(seq: Int, ts: String, regionId: String, blocks: [Block], contact: String = "") {
        self.seq = seq
        self.ts = ts
        self.regionId = regionId
        self.blocks = blocks
        self.contact = contact
    }

    enum CodingKeys: String, CodingKey {
        case type, seq, ts
        case regionId = "region_id"
        case blocks, contact
    }
}
```

- [ ] **Step 4: Run to verify they pass**
- Brain: `cd brain && ./.venv/bin/python -m pytest tests/test_protocol.py -v` → PASS.
- Swift: `cd shell && swift test --filter ProtocolTests 2>&1 | tail -5` → PASS.

- [ ] **Step 5: Commit**

```bash
git add brain/src/glimpse_brain/protocol.py shell/Sources/GlimpseShellLib/Protocol.swift brain/tests/test_protocol.py shell/Tests/GlimpseShellTests/ProtocolTests.swift
git commit -m "feat(protocol): optional OcrMsg.contact (the memory key)"
```

---

## Task 6: Config + server wiring (current customer, capture, memory construction)

**Files:**
- Modify: `brain/src/glimpse_brain/config.py`, `brain/src/glimpse_brain/server.py`
- Test: `brain/tests/test_config.py`, `brain/tests/test_server.py`

- [ ] **Step 1: Write the failing tests**

`brain/tests/test_config.py`:
```python
def test_memory_cfg_defaults() -> None:
    from glimpse_brain.config import Config

    cfg = Config()
    assert cfg.memory.recall_k == 5
    assert cfg.memory.embedding_model == "embeddinggemma"
    assert cfg.memory.enabled is True
```

`brain/tests/test_server.py` — add (using the existing `FakeToolClient` + a fake memory):
```python
async def test_server_captures_interactions_and_passes_customer(tmp_path: Path) -> None:
    # WHY: with a known contact, the brain auto-captures the interaction to that
    # customer's memory and scopes the agent to them.
    from glimpse_brain.memory import InMemoryMemory

    cfg = make_config(tmp_path)
    mem = InMemoryMemory()

    class CustomerCapturingClient:
        def __init__(self) -> None:
            self.seen_customer_had_memory = False

        async def run_turn(self, *, system: str, transcript: list, tools: list) -> "AgentStep":
            self.seen_customer_had_memory = any(t.name == "recall_customer" for t in tools)
            return AgentStep(final_text='["好的"]')

    client = CustomerCapturingClient()
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=client, memory=mem)
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(b'{"type":"hello","shell_version":"0.1.0"}\n')
        await writer.drain()
        await read_until(reader, "status")
        # OCR with a contact → memory scoped + interaction captured
        line = ('{"type":"ocr","seq":1,"ts":"t","region_id":"region-1","contact":"小明",'
                '"blocks":[{"text":"在吗，包邮吗？","x0":0.05,"x1":0.4,"conf":0.95}]}\n')
        writer.write(line.encode())
        await writer.drain()
        await read_until(reader, "suggestions")
        assert client.seen_customer_had_memory  # agent got memory tools for 小明
        recalled = await mem.recall("小明", "包邮", k=5)
        assert any(h.kind == "interaction" for h in recalled)  # captured
        writer.close()
    finally:
        task.cancel()
```

- [ ] **Step 2: Run to verify they fail** — config test FAIL (no `memory` cfg); server test FAIL (`memory` kwarg unknown).

- [ ] **Step 3: Implement**

(a) `config.py` — add a `MemoryCfg` class and a `memory` field on `Config`:
```python
class MemoryCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    palace_path: str = Field(default_factory=lambda: str(Path("~/.glimpse/palace").expanduser()))
    embedding_model: str = "embeddinggemma"
    recall_k: int = Field(default=5, ge=1, le=20)

    @field_validator("palace_path", mode="before")
    @classmethod
    def _expand(cls, v: str) -> str:
        return str(Path(v).expanduser())
```
And on `Config`, add (next to the other sub-configs):
```python
    memory: MemoryCfg = Field(default_factory=MemoryCfg)
```

(b) `server.py` — wire memory. Add a sentinel + import near the top:
```python
from glimpse_brain.memory import Memory

_UNSET: object = object()
```
Add `from glimpse_brain.memory import Memory` only (do NOT import mempalace at module top). Change `__init__` to accept `memory` and build a default lazily; store current customer; pass memory to the Agent:
```python
    def __init__(
        self,
        cfg: Config,
        llm: LLMClient | None = None,
        tool_client: ToolUseClient | None = None,
        memory: "Memory | None | object" = _UNSET,
    ) -> None:
        ...  # (keep existing redactor/events/tracker/shared_limiter setup)
        self._memory = self._build_memory(cfg) if memory is _UNSET else memory
        self._current_customer: str | None = None
        self._agent = Agent(
            client=tool_client if tool_client is not None
            else AnthropicToolUseClient(cfg.llm.model),
            system=AGENT_SYSTEM,
            knowledge=FileKnowledgeBase(playbook_path=Path(cfg.brain.playbook)),
            redactor=self._redactor,
            limiter=shared_limiter,
            max_suggestions=cfg.llm.max_suggestions,
            max_iterations=cfg.llm.max_iterations,
            memory=self._memory if isinstance(self._memory, Memory) or self._memory is not None else None,
            recall_k=cfg.memory.recall_k,
        )
        ...  # (keep summarizer + the rest)

    @staticmethod
    def _build_memory(cfg: Config):
        # Lazy, fail-soft: never imports mempalace unless enabled; None on any failure.
        if not cfg.memory.enabled:
            return None
        try:
            from glimpse_brain.mempalace_memory import MemPalaceMemory

            return MemPalaceMemory(
                palace_path=Path(cfg.memory.palace_path),
                embedding_model=cfg.memory.embedding_model,
            )
        except Exception:  # import/init failure → memory disabled, suggestions unaffected
            log.exception("memory disabled: MemPalaceMemory init failed")
            return None
```
(Simplify the Agent `memory=` argument to just `memory=self._memory` — `_build_memory`/injection already yields a `Memory` or `None`.)

In `_on_ocr`, set the current customer at the top and capture interactions after the observation append:
```python
    async def _on_ocr(self, msg: OcrMsg) -> None:
        await self._send(AckMsg(seq=msg.seq))
        self._region_id = msg.region_id
        self._current_customer = (msg.contact or "").strip() or None
        result = self._tracker.ingest(msg.blocks)
        if not result.accepted or not (result.new_inbound or result.new_outbound):
            return
        self._events.append(
            "observation",
            msg.region_id,
            {"inbound": result.new_inbound, "outbound": result.new_outbound},
        )
        if self._memory is not None and self._current_customer:
            for line in result.new_inbound + result.new_outbound:
                try:
                    await self._memory.write(
                        self._current_customer, self._redactor.redact(line), "interaction"
                    )
                except Exception:  # capture failure must not break the suggestion path
                    log.exception("memory capture failed")
        if result.new_inbound:
            await self._send(StatusMsg(state="thinking"))
            assert self._settle is not None
            self._settle.poke()
```

In `_fire`, pass the customer:
```python
            result = await self._agent.suggest(
                self._tracker.tail(), customer=self._current_customer
            )
```

In `AGENT_SYSTEM`, add a sentence after the knowledge_base line:
```
当你认识当前客户时，可调用 recall_customer 回忆其历史与偏好；发现值得长期记住的要点时，调用 remember_about_customer 记录。
```

- [ ] **Step 4: Run to verify** — `cd brain && ./.venv/bin/python -m pytest -q` → all pass (config + server + existing). Ruff: `./.venv/bin/ruff check src/ tests/` → clean. (Existing server tests that pass `tool_client=FakeToolClient()` but no `memory` now get the lazy default — but with `cfg.memory.enabled` True they'd try to build MemPalaceMemory. To keep those unit tests from importing mempalace, set `enabled=False` in `make_config` OR pass `memory=None`. **Update `make_config` in `test_server.py` to include `"memory": {"enabled": False}`** so existing tests stay memory-free; the new test injects `memory=mem` explicitly.)

- [ ] **Step 5: Commit**

```bash
git add brain/src/glimpse_brain/config.py brain/src/glimpse_brain/server.py brain/tests/test_config.py brain/tests/test_server.py
git commit -m "feat(brain): wire current-customer, deterministic capture, memory construction"
```

---

## Task 7: Shell — `ContactRegionStore` + calibration menu

**Files:**
- Create: `shell/Sources/GlimpseShellLib/ContactRegionStore.swift`
- Test: `shell/Tests/GlimpseShellTests/ContactRegionStoreTests.swift`
- Modify: `shell/Sources/GlimpseShell/main.swift` (menu item)

- [ ] **Step 1: Write the failing test** — create `shell/Tests/GlimpseShellTests/ContactRegionStoreTests.swift`:

```swift
import Foundation
import Testing
@testable import GlimpseShellLib

@Test
func contactRegionStoreRoundTripsRect() throws {
    let tmp = FileManager.default.temporaryDirectory
        .appendingPathComponent("cr-\(UUID()).json")
    defer { try? FileManager.default.removeItem(at: tmp) }
    let rect = CGRect(x: 10, y: 20, width: 300, height: 40)
    ContactRegionStore.save(rect, to: tmp)
    #expect(ContactRegionStore.load(from: tmp) == rect)
}

@Test
func contactRegionStoreFailsClosedOnMissingFile() {
    let missing = FileManager.default.temporaryDirectory
        .appendingPathComponent("nope-\(UUID()).json")
    #expect(ContactRegionStore.load(from: missing) == nil)
}
```

- [ ] **Step 2: Run to verify it fails** — `cd shell && swift test --filter ContactRegionStore 2>&1 | tail -5` → FAIL.

- [ ] **Step 3: Implement** — create `shell/Sources/GlimpseShellLib/ContactRegionStore.swift` (mirror of `RegionStore`, a separate file at `~/.glimpse/contact-region.json`):

```swift
import Foundation

/// Persists the calibrated contact-name region (CG rect). Mirror of RegionStore.
public enum ContactRegionStore {
    private struct Stored: Codable {
        var x: Double
        var y: Double
        var w: Double
        var h: Double
    }

    public static var url: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".glimpse/contact-region.json")
    }

    public static func save(_ rect: CGRect, to url: URL = ContactRegionStore.url) {
        try? FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        let stored = Stored(x: rect.minX, y: rect.minY, w: rect.width, h: rect.height)
        try? JSONEncoder().encode(stored).write(to: url)
    }

    public static func load(from url: URL = ContactRegionStore.url) -> CGRect? {
        guard let data = try? Data(contentsOf: url),
            let s = try? JSONDecoder().decode(Stored.self, from: data)
        else { return nil }
        return CGRect(x: s.x, y: s.y, width: s.w, height: s.h)
    }
}
```

Then in `shell/Sources/GlimpseShell/main.swift`, add a menu item after "设置输入框位置":
```swift
        menu.addItem(
            NSMenuItem(title: "设置联系人区域", action: #selector(calibrateContactRegion), keyEquivalent: "")
        )
```
and an action method (reusing `RegionSelector`, the existing drag-to-select-rect UI):
```swift
    @objc private func calibrateContactRegion() {
        contactSelector = RegionSelector { [weak self] rect in
            ContactRegionStore.save(rect)
            self?.contactSelector = nil
            self?.overlay.setStatus("watching", detail: "联系人区域已设置")
        }
        contactSelector?.begin()
    }
```
and a stored property `private var contactSelector: RegionSelector?` in `AppDelegate`.

- [ ] **Step 4: Run to verify** — `cd shell && swift test --filter ContactRegionStore 2>&1 | tail -5` → PASS; `swift build 2>&1 | tail -3` → builds.

- [ ] **Step 5: Commit**

```bash
git add shell/Sources/GlimpseShellLib/ContactRegionStore.swift shell/Tests/GlimpseShellTests/ContactRegionStoreTests.swift shell/Sources/GlimpseShell/main.swift
git commit -m "feat(shell): ContactRegionStore + calibration menu"
```

---

## Task 8: Shell — read the contact name + send it + show it

**Files:**
- Create: `shell/Sources/GlimpseShellLib/ContactReader.swift`
- Modify: `shell/Sources/GlimpseShellLib/Overlay.swift`, `shell/Sources/GlimpseShell/main.swift`

UI/capture task — verified by compile + the manual E2E checklist (no unit test; it
captures the screen). Reuses `ClickSnapshot.captureAround`-style one-shot capture + `OCR.recognize`.

- [ ] **Step 1: Create `ContactReader`** — `shell/Sources/GlimpseShellLib/ContactReader.swift`:

```swift
import AppKit

/// Periodically captures + OCRs the calibrated contact-name region and exposes the
/// latest detected name. Decoupled from the per-frame OCR path (names change
/// rarely), so the capture queue just reads `current`.
public final class ContactReader {
    private let store: () -> CGRect?
    private var timer: Timer?
    // Main-confined: written on main (timer), read via a main hop by the capture path.
    public private(set) var current: String = ""

    public init(region: @escaping () -> CGRect? = { ContactRegionStore.load() }) {
        self.store = region
    }

    public func start(interval: TimeInterval = 3) {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            self?.refresh()
        }
        refresh()
    }

    public func stop() {
        timer?.invalidate()
        timer = nil
    }

    private func refresh() {
        guard let rect = store() else { return }
        Task {
            guard let image = try? await ClickSnapshot.captureRect(rect) else { return }
            let name = ((try? OCR.recognize(image)) ?? [])
                .map(\.text).joined(separator: " ")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            await MainActor.run { self.current = name }
        }
    }
}
```
If `ClickSnapshot` does not already expose a rect capture, add a small helper to it:
```swift
    /// One-shot capture of an absolute CG rect (top-left origin) → CGImage.
    public static func captureRect(_ rect: CGRect) async throws -> CGImage? {
        // Reuse the same SCScreenshotManager path captureAround uses, but with the
        // given rect directly instead of computing one around a point.
        // (Mirror captureAround's implementation; substitute `rect` for the
        // computed rectangle.)
    }
```
Read `ClickSnapshot.swift`'s `captureAround` and factor its capture body so both
`captureAround` and `captureRect` share it (don't duplicate the SCStreamConfiguration setup).

- [ ] **Step 2: Wire it into `main.swift`** — add a `private let contactReader = ContactReader()` property; `contactReader.start()` in `applicationDidFinishLaunching`; and in `processFrame`, populate the contact on the `OcrMsg`:
```swift
        let message = OcrMsg(
            seq: seq, ts: isoFormatter.string(from: Date()),
            regionId: regionId, blocks: blocks, contact: contactReader.current
        )
```
And mirror the detected name into the overlay (next to the status):
```swift
        overlay.showContact(contactReader.current)
```

- [ ] **Step 3: Overlay name display** — in `Overlay.swift`, add to `OverlayModel` `@Published public var contact: String = ""`; an `OverlayController.showContact(_:)` that hops to main; and in `OverlayView.body`, render it in the status row when non-empty:
```swift
                if !model.contact.isEmpty {
                    Text("👤 \(model.contact)").font(.caption).foregroundColor(.secondary)
                }
```

- [ ] **Step 4: Build** — `cd shell && swift build 2>&1 | tail -3` → builds; `swift test 2>&1 | tail -3` → all pass.

- [ ] **Step 5: Commit**

```bash
git add shell/Sources/GlimpseShellLib/ContactReader.swift shell/Sources/GlimpseShellLib/ClickSnapshot.swift shell/Sources/GlimpseShellLib/Overlay.swift shell/Sources/GlimpseShell/main.swift
git commit -m "feat(shell): read contact name from calibrated region, send + display it"
```

---

## Task 9: Full suite + docs + manual E2E checklist

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run everything** — `cd brain && ./.venv/bin/python -m pytest -q` (all pass; integration NOT collected) and `./.venv/bin/ruff check src/ tests/` (clean); `cd shell && swift test 2>&1 | tail -3` (all pass). Report counts. If anything fails, STOP and report BLOCKED.

- [ ] **Step 2: Add a README section** matching the existing Phase-style headings:

```markdown
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
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: Phase 5 per-customer memory usage + integration test"
```

---

## Self-Review

**Spec coverage:**
- Contact-name identity (calibrated region → key, surfaced) → Tasks 5, 7, 8. ✓
- `Memory` Protocol + `MemPalaceMemory` (lib, pinned, embeddinggemma, write-spike up front) → Tasks 1, 2. ✓
- Hybrid capture (deterministic interactions + agent facts) → Task 6 (capture), Tasks 3/4 (`remember`). ✓
- Agent `recall_customer`/`remember_about_customer` bound to current customer → Tasks 3, 4. ✓
- Fail-soft everywhere (no customer → KB-only; memory init/recall/write failures swallowed) → Tasks 4, 6. ✓
- Redaction of stored + (recall path) content → Task 6 (capture redacts); recall hits are operator data surfaced like the conversation. ✓
- `OcrMsg.contact` optional/back-compat → Task 5. ✓
- MemPalace mapping (wing/room/drawer) + write-spike → Task 2. ✓
- Opt-in integration test + manual E2E → Tasks 2, 9. ✓

**Placeholder scan:** The only deliberate "spike" is Task 2's `_write_sync`, which is explicitly an exploration with a concrete acceptance test — not a silent TODO. The shell `captureRect` (Task 8) is described as "factor `captureAround`'s body and substitute the rect," with the instruction to read the existing file — acceptable for a UI capture task that mirrors existing code. All other steps have complete code.

**Type consistency:** `Memory.recall(customer, query, k)` / `write(customer, content, kind)` and `MemoryHit(text, kind, score)` (Task 1) are used identically in Tasks 2, 3, 4, 6. `Agent(..., memory=None, recall_k=5)` + `suggest(tail, customer=None)` (Task 4) match the server call (Task 6) and the agent tests. `RecallCustomerTool(memory, customer, k)` / `RememberAboutCustomerTool(memory, customer)` (Task 3) match agent usage (Task 4). `OcrMsg(..., contact="")` (Task 5) matches the shell send (Task 8) and the server read (Task 6). `MemoryCfg` fields (Task 6) match `MemPalaceMemory` construction. `ContactRegionStore.save/load(_:to:/from:)` (Task 7) match `ContactReader` usage (Task 8).

No gaps found.
