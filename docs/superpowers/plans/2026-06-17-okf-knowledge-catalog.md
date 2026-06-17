# OKF Knowledge Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Graduate the agent's knowledge base from a single whole-file `playbook.md` to an OKF catalog of markdown+frontmatter docs the agent navigates via an index + a read-by-id tool.

**Architecture:** A pure `okf.py` loader parses the catalog; `OkfKnowledgeBase` exposes `index()` (table of contents) and `read(id)` (one doc), re-scanning each call so hand-edits are live; two agent tools (`knowledge_base` → index, `read_knowledge{id}` → doc) replace the single whole-file tool. Fail-soft throughout; a legacy `playbook.md` is synthesized into a single doc for back-compat.

**Tech Stack:** Python 3.11 / pydantic / PyYAML / pytest (`asyncio_mode=auto`). The Swift shell is untouched (the `SuggestionsMsg` contract is unchanged).

**Spec:** `docs/superpowers/specs/2026-06-17-okf-knowledge-catalog-design.md`

**Conventions (every task):** `from __future__ import annotations`; PEP-604 `X | Y` unions (NOT `typing.Union`); full type annotations; frozen dataclasses for DTOs; interpreter `brain/.venv/bin/python`, linter `brain/.venv/bin/ruff`. Run all `git` commands from the repo root `/Users/john/Projects/visual-habit-tracking-agent`, as SEPARATE commands — do NOT chain `git commit` with `&&`, and never use `--amend`/`--no-verify` (a hook blocks them).

---

## File Structure

**Create:**
- `brain/src/glimpse_brain/okf.py` — pure OKF parse + catalog load.
- `brain/tests/test_okf.py`
- `playbook/knowledge/{example-a,shipping,returns,greeting,haggle,shipping-chase}.md` — seed catalog.

**Modify:**
- `brain/pyproject.toml` — declare `pyyaml`.
- `brain/src/glimpse_brain/knowledge.py` — `KnowledgeBase` protocol (`index`/`read`) + `OkfKnowledgeBase`; remove `FileKnowledgeBase`.
- `brain/src/glimpse_brain/config.py` — `BrainCfg.knowledge_dir`.
- `brain/src/glimpse_brain/tools.py` — `KnowledgeBaseTool` → index; new `ReadKnowledgeTool`.
- `brain/src/glimpse_brain/agent.py` — register both base tools.
- `brain/src/glimpse_brain/server.py` — build `OkfKnowledgeBase`; update `AGENT_SYSTEM`.
- `brain/evals/__main__.py` — build `OkfKnowledgeBase`.
- `brain/tests/{test_knowledge,test_tools,test_agent,test_server}.py` — adapt to the new surface.
- `README.md` — setup copies the seed catalog.

---

## Task 1: PyYAML dependency + `okf.py` loader

**Files:**
- Modify: `brain/pyproject.toml`
- Create: `brain/src/glimpse_brain/okf.py`
- Test: `brain/tests/test_okf.py`

- [ ] **Step 1: Declare the dependency**

In `brain/pyproject.toml`, change the `dependencies` line to add `pyyaml`:

```toml
dependencies = ["pydantic>=2.7", "anthropic>=0.40", "mempalace==3.4.0", "pyyaml>=6"]
```

(PyYAML 6.0.3 is already present transitively; this makes it explicit. No reinstall needed.)

- [ ] **Step 2: Write the failing tests**

Create `brain/tests/test_okf.py`:

```python
from __future__ import annotations

from pathlib import Path

from glimpse_brain.okf import OkfDoc, load_catalog, parse_doc


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


FULL = """\
---
id: shipping
title: 运费与发货政策
type: policy
tags: [包邮, 发货]
description: 满99包邮（偏远除外）
---

全场满 99 元包邮（偏远地区除外）。
"""


def test_parse_full_frontmatter(tmp_path: Path) -> None:
    doc = parse_doc(write(tmp_path / "shipping.md", FULL))
    assert doc == OkfDoc(
        id="shipping",
        title="运费与发货政策",
        type="policy",
        tags=["包邮", "发货"],
        description="满99包邮（偏远除外）",
        body="全场满 99 元包邮（偏远地区除外）。",
    )


def test_parse_derives_defaults_for_missing_keys(tmp_path: Path) -> None:
    # No frontmatter at all → id from filename, type "other", description = first line.
    doc = parse_doc(write(tmp_path / "greeting.md", "亲，您好～\n第二行"))
    assert doc.id == "greeting"
    assert doc.title == "greeting"
    assert doc.type == "other"
    assert doc.description == "亲，您好～"
    assert doc.tags == []
    assert doc.body == "亲，您好～\n第二行"


def test_load_catalog_skips_malformed_yaml(tmp_path: Path) -> None:
    write(tmp_path / "good.md", FULL)
    write(tmp_path / "bad.md", "---\ntitle: \"unterminated\ntype: [oops\n---\nbody\n")
    docs = load_catalog(tmp_path)
    ids = {d.id for d in docs}
    assert "shipping" in ids  # good survives
    assert "bad" not in ids   # malformed skipped, no crash


def test_load_catalog_dedups_ids_first_wins(tmp_path: Path) -> None:
    write(tmp_path / "a.md", "---\nid: dup\ntitle: A\n---\nfirst\n")
    write(tmp_path / "b.md", "---\nid: dup\ntitle: B\n---\nsecond\n")
    docs = [d for d in load_catalog(tmp_path) if d.id == "dup"]
    assert len(docs) == 1
    assert docs[0].title == "A"  # first wins (a.md sorts before b.md)


def test_load_catalog_recurses_subdirs(tmp_path: Path) -> None:
    write(tmp_path / "nested" / "deep.md", "---\nid: deep\ntype: policy\n---\nx\n")
    assert any(d.id == "deep" for d in load_catalog(tmp_path))


def test_load_catalog_back_compat_synthesizes_playbook(tmp_path: Path) -> None:
    # Empty/missing catalog dir + a legacy playbook → one synthesized "playbook" doc.
    legacy = write(tmp_path / "playbook.md", "# 客服话术\n满99包邮")
    docs = load_catalog(tmp_path / "knowledge", legacy_playbook=legacy)
    assert len(docs) == 1
    assert docs[0].id == "playbook"
    assert "满99包邮" in docs[0].body


def test_load_catalog_empty_is_empty(tmp_path: Path) -> None:
    assert load_catalog(tmp_path / "nope") == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd brain && .venv/bin/python -m pytest tests/test_okf.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'glimpse_brain.okf'`.

- [ ] **Step 4: Implement the loader**

Create `brain/src/glimpse_brain/okf.py`:

```python
"""Pure OKF catalog loader: parse markdown + YAML-frontmatter docs into OkfDoc
records. Adopts the OKF format only — no OKF tooling/stack."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger("glimpse.okf")


@dataclass(frozen=True)
class OkfDoc:
    id: str
    title: str
    type: str
    tags: list[str]
    description: str
    body: str


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter dict, body). A leading '---' fence delimits the YAML
    block; no fence → empty frontmatter and the whole text is the body."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            meta = yaml.safe_load(parts[1]) or {}
            if not isinstance(meta, dict):
                meta = {}
            return meta, parts[2].lstrip("\n")
    return {}, text


def parse_doc(path: Path) -> OkfDoc:
    """Parse one file into an OkfDoc, deriving defaults for missing keys.
    Raises yaml.YAMLError on malformed frontmatter (the loader skips it)."""
    text = path.read_text(encoding="utf-8")
    meta, body = _split_frontmatter(text)
    body = body.strip()
    doc_id = str(meta.get("id") or path.stem)
    first_line = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    raw_tags = meta.get("tags") or []
    tags = [str(t) for t in raw_tags] if isinstance(raw_tags, list) else [str(raw_tags)]
    return OkfDoc(
        id=doc_id,
        title=str(meta.get("title") or doc_id),
        type=str(meta.get("type") or "other"),
        tags=tags,
        description=str(meta.get("description") or first_line),
        body=body,
    )


def load_catalog(
    catalog_dir: Path, legacy_playbook: Path | None = None
) -> list[OkfDoc]:
    """Recursively load *.md docs under catalog_dir. Skip a doc with malformed
    frontmatter (warn); dedup ids (first wins, warn). If the dir is missing/empty
    but legacy_playbook exists, synthesize a single `playbook` doc (back-compat)."""
    docs: list[OkfDoc] = []
    seen: set[str] = set()
    paths = sorted(catalog_dir.glob("**/*.md")) if catalog_dir.exists() else []
    for path in paths:
        try:
            doc = parse_doc(path)
        except yaml.YAMLError:
            log.warning("skipping malformed OKF doc: %s", path, exc_info=True)
            continue
        if doc.id in seen:
            log.warning("duplicate OKF id %r (%s) — keeping the first", doc.id, path)
            continue
        seen.add(doc.id)
        docs.append(doc)
    if not docs and legacy_playbook is not None and legacy_playbook.exists():
        text = legacy_playbook.read_text(encoding="utf-8").strip()
        first = next(
            (ln.strip("# ").strip() for ln in text.splitlines() if ln.strip()),
            "playbook",
        )
        docs.append(
            OkfDoc(
                id="playbook",
                title="客服话术与政策",
                type="policy",
                tags=[],
                description=first,
                body=text,
            )
        )
    return docs
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd brain && .venv/bin/python -m pytest tests/test_okf.py -v`
Expected: PASS (7 passed).

- [ ] **Step 6: Lint**

Run: `cd brain && .venv/bin/ruff check src/glimpse_brain/okf.py tests/test_okf.py`
Expected: All checks passed.

- [ ] **Step 7: Commit**

```
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/pyproject.toml brain/src/glimpse_brain/okf.py brain/tests/test_okf.py
git commit -m "feat(brain): pure OKF catalog loader + pyyaml dep"
```

---

## Task 2: Config — `knowledge_dir`

**Files:**
- Modify: `brain/src/glimpse_brain/config.py`
- Test: `brain/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `brain/tests/test_config.py`:

```python
def test_knowledge_dir_default_expands_home() -> None:
    from glimpse_brain.config import Config

    cfg = Config()
    assert cfg.brain.knowledge_dir.endswith("/.glimpse/knowledge")
    assert "~" not in cfg.brain.knowledge_dir
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd brain && .venv/bin/python -m pytest tests/test_config.py -k knowledge_dir -v`
Expected: FAIL — `AttributeError: 'BrainCfg' object has no attribute 'knowledge_dir'`.

- [ ] **Step 3: Add the field**

In `brain/src/glimpse_brain/config.py`, in `BrainCfg`, add the field after `feedback_log` and extend the `_expand` validator's field list to include `"knowledge_dir"`:

```python
    knowledge_dir: str = Field(default_factory=lambda: str(Path("~/.glimpse/knowledge").expanduser()))

    @field_validator("socket_path", "event_log", "playbook", "feedback_log", "knowledge_dir", mode="before")
    @classmethod
    def _expand(cls, v: str) -> str:
        return str(Path(v).expanduser())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd brain && .venv/bin/python -m pytest tests/test_config.py -k knowledge_dir -v`
Expected: PASS.

- [ ] **Step 5: Lint**

Run: `cd brain && .venv/bin/ruff check src/glimpse_brain/config.py tests/test_config.py`

- [ ] **Step 6: Commit**

```
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/src/glimpse_brain/config.py brain/tests/test_config.py
git commit -m "feat(brain): knowledge_dir config path"
```

---

## Task 3: `OkfKnowledgeBase` + protocol change (remove `FileKnowledgeBase`)

**Files:**
- Modify: `brain/src/glimpse_brain/knowledge.py`
- Test: `brain/tests/test_knowledge.py` (replace its contents)

- [ ] **Step 1: Replace the test file**

Overwrite `brain/tests/test_knowledge.py` entirely with:

```python
from __future__ import annotations

from pathlib import Path

from glimpse_brain.knowledge import OkfKnowledgeBase


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_index_lists_docs_ordered_by_type_then_id(tmp_path: Path) -> None:
    write(tmp_path / "shipping.md", "---\nid: shipping\ntype: policy\ndescription: 包邮\n---\nx")
    write(tmp_path / "apple.md", "---\nid: apple\ntype: product\ndescription: 苹果\n---\nx")
    kb = OkfKnowledgeBase(catalog_dir=tmp_path)
    out = kb.index()
    assert "- [policy] shipping: 包邮" in out
    assert "- [product] apple: 苹果" in out
    # ordered by (type, id): policy < product
    assert out.index("shipping") < out.index("apple")


def test_read_returns_title_and_body(tmp_path: Path) -> None:
    write(tmp_path / "returns.md", "---\nid: returns\ntitle: 退换政策\n---\n七天无理由")
    kb = OkfKnowledgeBase(catalog_dir=tmp_path)
    out = kb.read("returns")
    assert "# 退换政策" in out
    assert "七天无理由" in out


def test_read_unknown_id_is_friendly(tmp_path: Path) -> None:
    kb = OkfKnowledgeBase(catalog_dir=tmp_path)
    assert "未找到文档" in kb.read("nope")


def test_index_empty_catalog(tmp_path: Path) -> None:
    kb = OkfKnowledgeBase(catalog_dir=tmp_path / "missing")
    assert "知识库为空" in kb.index()


def test_back_compat_legacy_playbook(tmp_path: Path) -> None:
    legacy = tmp_path / "playbook.md"
    legacy.write_text("# 政策\n满99包邮", encoding="utf-8")
    kb = OkfKnowledgeBase(catalog_dir=tmp_path / "knowledge", legacy_playbook=legacy)
    assert "playbook" in kb.index()
    assert "满99包邮" in kb.read("playbook")


def test_rescans_live_on_each_call(tmp_path: Path) -> None:
    # WHY: hand-edits to the catalog must take effect without a restart.
    kb = OkfKnowledgeBase(catalog_dir=tmp_path)
    assert "知识库为空" in kb.index()
    write(tmp_path / "new.md", "---\nid: new\ntype: policy\ndescription: 新增\n---\nx")
    assert "new" in kb.index()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && .venv/bin/python -m pytest tests/test_knowledge.py -v`
Expected: FAIL — `ImportError: cannot import name 'OkfKnowledgeBase'`.

- [ ] **Step 3: Rewrite `knowledge.py`**

Overwrite `brain/src/glimpse_brain/knowledge.py` entirely with:

```python
"""Knowledge the agent retrieves via tools. An OKF catalog: the agent reads an
index (table of contents), then reads docs by id. Replaces the v1 whole-file
FileKnowledgeBase."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from glimpse_brain.okf import load_catalog


class KnowledgeBase(Protocol):
    def index(self) -> str: ...
    def read(self, doc_id: str) -> str: ...


class OkfKnowledgeBase:
    """OKF-catalog knowledge. Re-scans the catalog each call so hand-edits to the
    markdown take effect live. Falls back to a single legacy playbook.md."""

    def __init__(self, catalog_dir: Path, legacy_playbook: Path | None = None) -> None:
        self._catalog_dir = catalog_dir
        self._legacy_playbook = legacy_playbook

    def index(self) -> str:
        docs = load_catalog(self._catalog_dir, self._legacy_playbook)
        if not docs:
            return "（知识库为空）"
        ordered = sorted(docs, key=lambda d: (d.type, d.id))
        lines = [f"- [{d.type}] {d.id}: {d.description}" for d in ordered]
        return "知识库目录：\n" + "\n".join(lines)

    def read(self, doc_id: str) -> str:
        for doc in load_catalog(self._catalog_dir, self._legacy_playbook):
            if doc.id == doc_id:
                return f"# {doc.title}\n\n{doc.body}"
        return f"（未找到文档：{doc_id}）"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && .venv/bin/python -m pytest tests/test_knowledge.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Lint**

Run: `cd brain && .venv/bin/ruff check src/glimpse_brain/knowledge.py tests/test_knowledge.py`

- [ ] **Step 6: Commit**

```
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/src/glimpse_brain/knowledge.py brain/tests/test_knowledge.py
git commit -m "feat(brain): OkfKnowledgeBase (index+read); remove FileKnowledgeBase"
```

Note: this commit leaves `tools.py`, `agent.py`, `server.py`, `evals/__main__.py` referencing the old `grounding`/`FileKnowledgeBase` — they are fixed in Tasks 4–6. The full suite will not be green until Task 6; that is expected and called out in each task.

---

## Task 4: Tools — `knowledge_base` (index) + `read_knowledge`

**Files:**
- Modify: `brain/src/glimpse_brain/tools.py`
- Test: `brain/tests/test_tools.py` (replace its contents)

- [ ] **Step 1: Replace the test file**

Overwrite `brain/tests/test_tools.py` entirely with:

```python
from __future__ import annotations

from glimpse_brain.tools import KnowledgeBaseTool, ReadKnowledgeTool


class FakeKB:
    def index(self) -> str:
        return "知识库目录：\n- [policy] shipping: 包邮"

    def read(self, doc_id: str) -> str:
        return f"read:{doc_id}"


async def test_knowledge_base_tool_returns_index() -> None:
    tool = KnowledgeBaseTool(FakeKB())
    assert tool.name == "knowledge_base"
    assert tool.input_schema.get("required", []) == []  # no args needed
    out = await tool.run({})
    assert "知识库目录" in out


async def test_read_knowledge_tool_reads_by_id() -> None:
    tool = ReadKnowledgeTool(FakeKB())
    assert tool.name == "read_knowledge"
    assert tool.input_schema["properties"]["id"]["type"] == "string"
    assert tool.input_schema["required"] == ["id"]
    out = await tool.run({"id": "shipping"})
    assert out == "read:shipping"


async def test_read_knowledge_tool_tolerates_missing_id() -> None:
    # WHY: the model may emit the tool call with no input; must not KeyError.
    tool = ReadKnowledgeTool(FakeKB())
    out = await tool.run({})
    assert out == "read:"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && .venv/bin/python -m pytest tests/test_tools.py -v`
Expected: FAIL — `ImportError: cannot import name 'ReadKnowledgeTool'`.

- [ ] **Step 3: Rewrite `tools.py`**

Replace the `KnowledgeBaseTool` class in `brain/src/glimpse_brain/tools.py` and add `ReadKnowledgeTool`. The file becomes (keep the existing header docstring, `from __future__`, imports, and the `Tool` Protocol unchanged):

```python
class KnowledgeBaseTool:
    name = "knowledge_base"
    description = "返回知识库目录（各文档 id/类型/摘要）；先调用它了解有哪些资料，再用 read_knowledge 读取相关文档。"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    def __init__(self, knowledge: KnowledgeBase) -> None:
        self._knowledge = knowledge

    async def run(self, input: dict[str, Any]) -> str:
        return self._knowledge.index()


class ReadKnowledgeTool:
    name = "read_knowledge"
    description = "按 id 读取某篇知识库文档的完整内容。可并行读取多篇。"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "文档 id（见 knowledge_base 目录）"}
        },
        "required": ["id"],
    }

    def __init__(self, knowledge: KnowledgeBase) -> None:
        self._knowledge = knowledge

    async def run(self, input: dict[str, Any]) -> str:
        return self._knowledge.read(input.get("id", ""))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && .venv/bin/python -m pytest tests/test_tools.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Lint**

Run: `cd brain && .venv/bin/ruff check src/glimpse_brain/tools.py tests/test_tools.py`

- [ ] **Step 6: Commit**

```
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/src/glimpse_brain/tools.py brain/tests/test_tools.py
git commit -m "feat(brain): knowledge_base->index tool + read_knowledge tool"
```

---

## Task 5: Agent — register both knowledge tools

**Files:**
- Modify: `brain/src/glimpse_brain/agent.py`
- Test: `brain/tests/test_agent.py`

**Scene:** `Agent.__init__` builds `self._base_tools`. `suggest` runs the tool-use loop; tests inject a scripted `ToolUseClient`. The existing `test_agent.py` has a fake client pattern (it scripts `run_turn` to emit `AgentStep`s). We add a test proving the two-step knowledge path works, and register the second tool.

- [ ] **Step 1: Write the failing test**

First read `brain/tests/test_agent.py` to match its existing fakes (the `ToolUseClient` fake, `AgentStep`/`ToolCall` imports, and how `Agent` is constructed with a `knowledge=` whose type now has `index`/`read`). Then add this test (adapt the construction to match the file's existing helper for building an `Agent`; the key assertions are the two tool names):

```python
async def test_agent_uses_index_then_read() -> None:
    from glimpse_brain.agent import Agent
    from glimpse_brain.tooluse import AgentStep, ToolCall
    from glimpse_brain.llm import RateLimiter
    from glimpse_brain.redaction import Redactor

    class FakeKB:
        def __init__(self) -> None:
            self.read_calls: list[str] = []

        def index(self) -> str:
            return "知识库目录：\n- [policy] shipping: 包邮"

        def read(self, doc_id: str) -> str:
            self.read_calls.append(doc_id)  # spy: proves the read tool ran
            return f"read:{doc_id}"

    class ScriptedClient:
        def __init__(self) -> None:
            self._calls = 0

        async def run_turn(self, *, system, transcript, tools) -> AgentStep:
            self._calls += 1
            if self._calls == 1:
                return AgentStep(tool_calls=(ToolCall(id="t1", name="knowledge_base", input={}),))
            if self._calls == 2:
                return AgentStep(tool_calls=(ToolCall(id="t2", name="read_knowledge", input={"id": "shipping"}),))
            return AgentStep(final_text='["好的，亲，满99包邮哦"]')

    kb = FakeKB()
    agent = Agent(
        client=ScriptedClient(),
        system="sys",
        knowledge=kb,
        redactor=Redactor([]),
        limiter=RateLimiter(60),
        max_suggestions=3,
        max_iterations=5,
    )
    result = await agent.suggest(["客户: 包邮吗"])
    # WHY this is the load-bearing assertion: the agent loop appends every called
    # tool NAME to tools_used *before* the registry lookup, so "read_knowledge" in
    # tools_used would be true even if the tool were unregistered. The spy proves
    # the tool was actually FOUND and EXECUTED — which only happens once
    # ReadKnowledgeTool is registered in _base_tools.
    assert kb.read_calls == ["shipping"]
    assert "knowledge_base" in result.tools_used
    assert result.drafts == ["好的，亲，满99包邮哦"]
```

NOTE: if `test_agent.py`'s existing fakes use a different `AgentStep`/`ToolCall` construction (a helper or different field names), mirror that exact style instead — verify by reading the file first. Do not invent a `ToolCall` signature; use the one the codebase defines in `glimpse_brain.tooluse` (confirm `AgentStep` can be built with `tool_calls=` only and with `final_text=` only — the existing `test_server.py` `FakeToolClient` already builds it with `final_text=` only).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd brain && .venv/bin/python -m pytest tests/test_agent.py -k index_then_read -v`
Expected: FAIL — `ReadKnowledgeTool` is not yet in `_base_tools`, so the registry lookup returns `None` (the loop emits `unknown tool: read_knowledge`), `FakeKB.read` is never called, and `kb.read_calls == ["shipping"]` fails (it is `[]`).

- [ ] **Step 3: Register the second tool**

In `brain/src/glimpse_brain/agent.py`, update the import and `_base_tools`:

```python
from glimpse_brain.tools import KnowledgeBaseTool, ReadKnowledgeTool, Tool
```

```python
        self._base_tools: list[Tool] = [
            KnowledgeBaseTool(knowledge),
            ReadKnowledgeTool(knowledge),
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd brain && .venv/bin/python -m pytest tests/test_agent.py -k index_then_read -v`
Expected: PASS.

- [ ] **Step 5: Run the whole agent test file** (catch any fake that still uses `grounding`)

Run: `cd brain && .venv/bin/python -m pytest tests/test_agent.py -v`
Expected: PASS. If any existing test in this file builds a fake KB with a `grounding` method, update that fake to expose `index`/`read` (the new protocol) — the agent never calls `grounding` anymore.

- [ ] **Step 6: Lint**

Run: `cd brain && .venv/bin/ruff check src/glimpse_brain/agent.py tests/test_agent.py`

- [ ] **Step 7: Commit**

```
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/src/glimpse_brain/agent.py brain/tests/test_agent.py
git commit -m "feat(brain): agent registers knowledge_base + read_knowledge"
```

---

## Task 6: Wire `OkfKnowledgeBase` into server + evals; update `AGENT_SYSTEM`

**Files:**
- Modify: `brain/src/glimpse_brain/server.py`
- Modify: `brain/evals/__main__.py`
- Test: `brain/tests/test_server.py` (update `make_config`)

**Scene:** `server.py` builds the `Agent` with `knowledge=FileKnowledgeBase(playbook_path=...)` and defines `AGENT_SYSTEM`. `evals/__main__.py` does the same in `_run`. Both must switch to `OkfKnowledgeBase`. After this task the full suite goes green.

- [ ] **Step 1: Update `make_config` in `test_server.py`**

In `brain/tests/test_server.py`, add a `knowledge_dir` key to `make_config`'s `"brain"` dict (so server tests use a tmp catalog dir — missing, so it falls back to the tmp `playbook.md`):

```python
            "brain": {
                "socket_path": str(tmp_path / "glimpse.sock"),
                "event_log": str(tmp_path / "events.jsonl"),
                "playbook": str(playbook),
                "feedback_log": str(tmp_path / "feedback.jsonl"),
                "knowledge_dir": str(tmp_path / "knowledge"),
            },
```

- [ ] **Step 2: Run the server suite to verify the failure**

Run: `cd brain && .venv/bin/python -m pytest tests/test_server.py -q`
Expected: FAIL at import/collection — `server.py` still imports `FileKnowledgeBase`, which no longer exists (removed in Task 3). This confirms Task 6 is needed.

- [ ] **Step 3: Switch `server.py` to `OkfKnowledgeBase`**

In `brain/src/glimpse_brain/server.py`:

(a) Replace the import:
```python
from glimpse_brain.knowledge import OkfKnowledgeBase
```

(b) Replace the `knowledge=` argument in the `Agent(...)` construction:
```python
            knowledge=OkfKnowledgeBase(
                catalog_dir=Path(cfg.brain.knowledge_dir),
                legacy_playbook=Path(cfg.brain.playbook),
            ),
```

(c) Update `AGENT_SYSTEM` — replace the existing knowledge line (currently `你可以调用 knowledge_base 工具获取产品信息、政策和话术——起草任何依赖这些信息的回复前都应先调用它。`) with:
```
起草任何依赖产品信息/政策/话术的回复前，先调用 knowledge_base 查看知识库目录，再用 read_knowledge 按 id 读取相关文档；正文中的 [[id]] 是交叉引用，可继续 read_knowledge。
```

- [ ] **Step 4: Switch `evals/__main__.py` to `OkfKnowledgeBase`**

In `brain/evals/__main__.py`:

(a) Replace the import:
```python
from glimpse_brain.knowledge import OkfKnowledgeBase
```

(b) Replace the `knowledge=` argument in the `Agent(...)` construction inside `_run`:
```python
        knowledge=OkfKnowledgeBase(
            catalog_dir=Path(cfg.brain.knowledge_dir),
            legacy_playbook=Path(cfg.brain.playbook),
        ),
```

- [ ] **Step 5: Run the server suite, then the FULL brain suite**

Run: `cd brain && .venv/bin/python -m pytest tests/test_server.py -q`
Expected: PASS.

Run: `cd brain && .venv/bin/python -m pytest -q`
Expected: ALL pass (1 integration test deselected). This is the point where the whole brain is green again.

- [ ] **Step 6: Lint the whole brain**

Run: `cd brain && .venv/bin/ruff check src tests evals`
Expected: All checks passed.

- [ ] **Step 7: Commit**

```
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/src/glimpse_brain/server.py brain/evals/__main__.py brain/tests/test_server.py
git commit -m "feat(brain): wire OkfKnowledgeBase into server + evals; two-step AGENT_SYSTEM"
```

---

## Task 7: Seed catalog + README setup

**Files:**
- Create: `playbook/knowledge/example-a.md`, `shipping.md`, `returns.md`, `greeting.md`, `haggle.md`, `shipping-chase.md`
- Modify: `README.md`

- [ ] **Step 1: Create the six seed docs**

Create `playbook/knowledge/example-a.md`:
```markdown
---
id: example-a
title: 示例商品 A
type: product
tags: [主打, 现货]
description: 主打商品 A，99 元，现货
---

主打商品：示例商品 A，售价 99 元，现货供应。
退换与运费见 [[returns]]，包邮规则见 [[shipping]]。
```

Create `playbook/knowledge/shipping.md`:
```markdown
---
id: shipping
title: 运费与发货政策
type: policy
tags: [包邮, 发货]
description: 满99包邮（偏远除外）；工作日16:00前付款当天发货
---

全场满 99 元包邮（偏远地区除外）。工作日 16:00 前付款，当天发货。
```

Create `playbook/knowledge/returns.md`:
```markdown
---
id: returns
title: 退换政策
type: policy
tags: [退换, 运费险]
description: 七天无理由退换，运费险自动赠送
---

七天无理由退换，运费险自动赠送。
```

Create `playbook/knowledge/greeting.md`:
```markdown
---
id: greeting
title: 欢迎语话术
type: script
tags: [欢迎]
description: 欢迎语
---

亲，您好～有什么可以帮您的吗？
```

Create `playbook/knowledge/haggle.md`:
```markdown
---
id: haggle
title: 议价话术
type: script
tags: [议价]
description: 议价时强调活动价与包邮，不直接降价
---

亲，我们已经是活动价啦，满 99 还包邮哦～
参见 [[shipping]] 的包邮规则。
```

Create `playbook/knowledge/shipping-chase.md`:
```markdown
---
id: shipping-chase
title: 催发货话术
type: script
tags: [催发货]
description: 催发货给具体时间点
---

您的订单我们会尽快安排，工作日 16:00 前付款当天就能发出～
```

- [ ] **Step 2: Verify the seed catalog loads and indexes cleanly**

Run:
```
cd brain && .venv/bin/python -c "from glimpse_brain.knowledge import OkfKnowledgeBase; from pathlib import Path; kb = OkfKnowledgeBase(catalog_dir=Path('../playbook/knowledge')); print(kb.index()); print('---'); print(kb.read('haggle'))"
```
Expected: an index listing all six docs grouped by type, and the `haggle` doc body. No warnings about malformed/duplicate docs.

- [ ] **Step 3: Update the README setup step**

In `README.md`, find the Setup block line that copies the playbook:
```bash
mkdir -p ~/.glimpse && cp ../playbook/playbook.md ~/.glimpse/playbook.md
```
Replace it with (keep both — the catalog is primary, the single file remains the back-compat fallback):
```bash
mkdir -p ~/.glimpse
cp -r ../playbook/knowledge ~/.glimpse/knowledge   # OKF catalog (primary)
cp ../playbook/playbook.md ~/.glimpse/playbook.md  # legacy fallback
```

Then add, after the Phase 6 section (end of file), a short subsection:
```markdown
## Knowledge base — OKF catalog

Grounding lives in `~/.glimpse/knowledge/` as OKF docs (markdown + YAML
frontmatter: `id`, `title`, `type`, `tags`, `description`). The agent calls
`knowledge_base` to see the index, then `read_knowledge{id}` for the docs it
needs. Edit/add `.md` files there (nest into subfolders if you like — the catalog
is scanned recursively); changes are picked up live. If the directory is absent,
the agent falls back to the single `~/.glimpse/playbook.md`.
```

- [ ] **Step 4: Commit**

```
cd /Users/john/Projects/visual-habit-tracking-agent
git add playbook/knowledge README.md
git commit -m "feat: seed OKF knowledge catalog + README setup"
```

---

## Final verification (after all tasks)

- [ ] **Full brain suite green:** `cd brain && .venv/bin/python -m pytest -q` — all pass, 1 integration deselected.
- [ ] **Ruff clean:** `cd brain && .venv/bin/ruff check src tests evals`.
- [ ] **Shell untouched but still green:** `cd shell && swift test 2>&1 | tail -3` (no shell files changed; confirms nothing collateral broke).
- [ ] **No lingering `FileKnowledgeBase`/`.grounding(`:** `grep -rn "FileKnowledgeBase\|\.grounding(" brain/src brain/tests brain/evals` returns nothing.
- [ ] **Seed catalog loads:** the Task 7 Step 2 command prints a clean six-entry index.
```
