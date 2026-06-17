# OKF Knowledge Catalog (Design)

**Date:** 2026-06-17
**Status:** Approved (pending spec review)
**Keystone:** `2026-06-14-ai-native-architecture.md` (the KB-construction track)
**Builds on:** P4 agentic core (the `KnowledgeBase`/tool seam this graduates).
**Reference:** [[okf-kb-format-reference]] — Google OKF (Apache-2.0 spec, markdown +
YAML frontmatter + directory hierarchy). We adopt the **format**, not the POC stack.

## Goal

Graduate the agent's knowledge base from a single whole-file `playbook.md` (dumped
in full on every `knowledge_base` call, ignoring the query) to an **OKF catalog** of
small markdown+frontmatter docs the agent **navigates**: it first reads a compact
index (table of contents), then reads only the docs it needs. This gives targeted,
token-efficient grounding that scales as the catalog grows, in a human- and
agent-editable, git-versionable, vendor-neutral format.

## Design Principles

- **Adopt OKF's format, not its stack.** Markdown + YAML frontmatter + directory
  hierarchy + `[[id]]` cross-links. None of the OKF POC's ADK/Gemini/BigQuery/
  web-crawler enrichment machinery.
- **Agent-driven access (AI-native).** The agent browses an index and decides what
  to read — consistent with how it already decides to recall memory or look at an
  image. No embedding index in v1 (YAGNI for a small catalog; deferrable).
- **Fail-soft.** Any catalog problem (missing dir, malformed doc, unknown id)
  degrades gracefully; drafting never crashes on knowledge.
- **Live edits.** The catalog is re-scanned each call, so hand-edits to the
  markdown take effect immediately — preserving today's `playbook.md` ergonomics.
- **Graceful migration.** An existing install with only the legacy `playbook.md`
  keeps working with no manual step.

## Scope

### In scope
- An OKF document format + a pure parser/loader (`okf.py`).
- `OkfKnowledgeBase` behind a two-method `KnowledgeBase` protocol (`index` + `read`).
- Two agent tools: `knowledge_base` (returns the index) and `read_knowledge{id}`.
- `AGENT_SYSTEM` two-step guidance; `config.py`/`server.py`/`evals` wiring.
- A seed catalog (the current `playbook.md` converted to ~6 OKF docs) shipped in
  the repo and copied to `~/.glimpse/knowledge/` on setup.
- Removal of the superseded `FileKnowledgeBase` (+ its vestigial `learnings_path`).

### Out of scope (YAGNI / deferred)
- **Embedding/semantic retrieval** over the catalog (defer until it's large).
- **`[[id]]` link resolution** — links are left as text; the agent follows one by
  calling `read_knowledge` on that id.
- **Structured product fields** (sku/price/stock) — added when the SKU/product-graph
  work (P6b/c) defines them, not now.
- **A catalog viewer / authoring UI** — docs are edited on disk / in git.
- **Per-doc access control, versioning beyond git, multi-catalog merge.**

## The OKF document format

Each `.md` file under the catalog dir:

```markdown
---
id: shipping
title: 运费与发货政策
type: policy            # product | policy | script | other
tags: [包邮, 发货, 运费险]
description: 满99包邮（偏远除外）；工作日16:00前付款当天发货
---

全场满 99 元包邮（偏远地区除外）。工作日 16:00 前付款当天发货。
正文是自由 markdown，可用 [[returns]] 交叉引用其他文档。
```

- `id` = the filename stem; unique across the catalog (first wins on collision).
- `description` is the one-line index summary (load-bearing — it's what the agent
  sees before reading).
- Body is free markdown; `[[id]]` is a plain-text cross-link convention.

## Architecture

### `okf.py` (new, pure)
| Unit | Responsibility |
|---|---|
| `OkfDoc` | Frozen dataclass: `id, title, type, tags: list[str], description, body`. |
| `parse_doc(path)` | Parse one file → `OkfDoc`. Split YAML frontmatter from body; derive defaults for missing keys (`id`=stem, `title`=id, `type`="other", `description`=first non-empty body line, `tags`=[]). Raise on malformed YAML (caught by the loader). |
| `load_catalog(catalog_dir, legacy_playbook)` | Recursively glob `**/*.md`; `parse_doc` each (skip + warn on a parse error); dedup ids (first wins, warn). If the dir is missing/empty but `legacy_playbook` exists, return a single synthesized `playbook` doc. Returns `list[OkfDoc]`. |

### `knowledge.py` (changed)
The `KnowledgeBase` protocol gains two methods (replacing `grounding`):
```python
class KnowledgeBase(Protocol):
    def index(self) -> str: ...           # the table of contents
    def read(self, doc_id: str) -> str: ...
```
`OkfKnowledgeBase(catalog_dir, legacy_playbook)`:
- `index()` → `load_catalog(...)`, then one line per doc, ordered by (type, id):
  `- [{type}] {id}: {description}`. Empty catalog → `（知识库为空）`.
- `read(doc_id)` → `load_catalog(...)`, find by id → `# {title}\n\n{body}`; unknown
  id → `（未找到文档：{doc_id}）`.
- Re-scans each call (live edits). `FileKnowledgeBase` is removed.

### `tools.py` (changed)
| Tool | Responsibility |
|---|---|
| `KnowledgeBaseTool` (`knowledge_base`) | No required args → returns `kb.index()`. Description: 返回知识库目录（各文档 id/类型/摘要）；先调用它了解有哪些资料。 |
| `ReadKnowledgeTool` (`read_knowledge`) | input `{id: str}` → returns `kb.read(id)`. Description: 按 id 读取某篇知识库文档的完整内容。可并行读取多篇。 |

### `agent.py` (changed)
Base tools become `[KnowledgeBaseTool(knowledge), ReadKnowledgeTool(knowledge)]`
(both built from the injected `knowledge`). No change to the loop.

### `AGENT_SYSTEM` (server.py, changed)
Replace the single-tool knowledge line with: 先调用 knowledge_base 查看知识库目录，
再用 read_knowledge 按 id 读取相关文档；正文中的 [[id]] 是交叉引用，可继续 read_knowledge。

### `config.py` (changed)
`BrainCfg.knowledge_dir` (default `~/.glimpse/knowledge`, `_expand`ed). `playbook`
stays as the legacy fallback source.

### Wiring (server.py, evals/__main__.py)
Both construct `OkfKnowledgeBase(catalog_dir=Path(cfg.brain.knowledge_dir),
legacy_playbook=Path(cfg.brain.playbook))` and pass it to `Agent` (which now
registers both tools).

### Seed catalog + setup
`playbook/knowledge/` in the repo holds the converted docs:
`example-a.md` (product); `shipping.md`, `returns.md` (policy); `greeting.md`,
`haggle.md`, `shipping-chase.md` (script). `scripts/dev.sh` and the README setup
step copy `playbook/knowledge/` → `~/.glimpse/knowledge/` (alongside the existing
`playbook.md` copy, which remains as the fallback).

## Data Flow

```
turn 1: agent calls knowledge_base (no args)
  → KnowledgeBaseTool → kb.index():
      知识库目录：
      - [product] example-a: 主打商品A，99元，现货
      - [policy] shipping: 满99包邮（偏远除外）；16:00前当天发货
      - [policy] returns: 七天无理由退换，运费险
      - [script] greeting: 欢迎语
      ...
turn 2: agent calls read_knowledge{id:"shipping"} (+ others in parallel)
  → ReadKnowledgeTool → kb.read("shipping") → # 运费与发货政策\n\n<body>
agent drafts a grounded reply (SuggestionsMsg contract unchanged)
```

## Error Handling (all fail-soft)

| Condition | Behavior |
|---|---|
| Catalog dir missing/empty + legacy `playbook.md` present | synthesize one `playbook` doc |
| Catalog dir missing/empty + no legacy | `index()` → `（知识库为空）`; `read()` → not-found |
| Malformed YAML frontmatter in a doc | skip that doc (warn), keep the rest |
| Missing frontmatter keys | derive defaults (id=stem, title=id, type="other", description=first body line, tags=[]) |
| Duplicate `id` across files | first wins, warn |
| `read_knowledge` unknown id | `（未找到文档：{id}）`; agent proceeds |

## Testing (pytest, fakes — no API)

**`okf.py` (pure):** full-frontmatter parse → correct `OkfDoc` with body separated;
missing keys → derived defaults; malformed YAML → `load_catalog` skips it while
siblings survive; duplicate id → first wins; recursive scan finds a nested-subdir doc.

**`OkfKnowledgeBase`:** `index()` lists each doc `- [type] id: description`,
deterministically ordered by (type, id); `read(id)` returns title+body; unknown id
→ not-found string; **back-compat** — empty dir + legacy playbook → a `playbook` doc
in the index, readable by id; **live re-scan** — a file added between two `index()`
calls appears.

**Tools:** `KnowledgeBaseTool.run` → the index; `ReadKnowledgeTool.run({"id": ...})`
→ that doc; both advertise correct `input_schema`.

**`Agent` (scripted client):** a client that calls `knowledge_base` then
`read_knowledge` → `tools_used` contains both and the agent finalizes. *Intent: the
two-step grounding path works end-to-end.*

**Server/evals wiring:** both build `OkfKnowledgeBase` and register both tools;
`make_config` in `test_server.py` gains a tmp `knowledge_dir` (missing → falls back
to the tmp `playbook.md`), so existing server tests keep passing unchanged.

**Coverage target:** ≥80% on the pure units (`okf` parsing, `OkfKnowledgeBase`, the
two tools, the agent path). Seed-catalog content is data, eyeballed manually.

## Dependencies

YAML frontmatter parsing needs a YAML reader. Prefer the standard `PyYAML` (add to
`brain/pyproject.toml`) over hand-rolling; parse only the frontmatter block (split on
the leading `---` fence) and `yaml.safe_load` it.
