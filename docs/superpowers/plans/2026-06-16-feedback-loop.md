# Feedback-Capture Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the human rate each suggestion 👍/👎 with an optional correction, feeding memory (immediate recall), a durable corpus → offline-distilled candidate eval cases, and an advisory-only satisfaction metric.

**Architecture:** A live, model-free capture path in the brain (audit event + redacted `feedback.jsonl` corpus + P5 memory write + rolling-rate advisory) plus an offline, billable `evals distill` command that turns corrections into candidate eval cases under a non-gating `cases/candidates/` directory. The advisory emits text only — structurally no wire path to the `自动发送` toggle.

**Tech Stack:** Python 3.11 / pydantic / pytest (`asyncio_mode=auto`); Swift 5.9 / AppKit+SwiftUI / Swift Testing; NDJSON over a Unix socket; the `anthropic` SDK (offline distiller only).

**Spec:** `docs/superpowers/specs/2026-06-15-feedback-loop-design.md`

---

## File Structure

**Brain (create):**
- `brain/src/glimpse_brain/satisfaction.py` — `SatisfactionTracker` pure rolling-rate core.
- `brain/src/glimpse_brain/feedback.py` — `FeedbackRecord` dataclass + `FeedbackLog` (redact-on-write corpus).
- `brain/src/glimpse_brain/evals_pkg/distill.py` — pure record→prompt + response→case-dict + deterministic id.

**Brain (modify):**
- `brain/src/glimpse_brain/protocol.py` — `FeedbackMsg` (inbound) + `AdvisoryMsg` (outbound).
- `brain/src/glimpse_brain/config.py` — `BrainCfg.feedback_log` + `FeedbackCfg`.
- `brain/src/glimpse_brain/server.py` — snapshot cache, `_on_feedback`, startup seeding, send advisory.
- `brain/evals/__main__.py` — `distill` / `promote` subcommands.

**Shell (modify):**
- `shell/Sources/GlimpseShellLib/Protocol.swift` — `FeedbackMsg` encode, `AdvisoryMsg` decode, `BrainMessage.advisory`.
- `shell/Sources/GlimpseShellLib/Overlay.swift` — per-card 👍/👎 + note field, advisory line, `onFeedback`.
- `shell/Sources/GlimpseShell/main.swift` — wire `onFeedback`→`FeedbackMsg`, handle `.advisory`.

**Repo:**
- `.gitignore` — ignore `brain/evals/cases/candidates/`.
- `README.md` — E2E checklist entries.

---

## Task 1: Wire protocol — FeedbackMsg + AdvisoryMsg

**Files:**
- Modify: `brain/src/glimpse_brain/protocol.py`
- Test: `brain/tests/test_protocol.py`

- [ ] **Step 1: Write the failing tests**

Add to `brain/tests/test_protocol.py`:

```python
def test_feedback_msg_round_trips() -> None:
    from glimpse_brain.protocol import FeedbackMsg, parse_inbound, to_line

    msg = FeedbackMsg(suggestion_id="s1", region_id="region-1", verdict="down", note="强调赠品")
    parsed = parse_inbound(to_line(msg).strip())
    assert isinstance(parsed, FeedbackMsg)
    assert parsed.verdict == "down"
    assert parsed.note == "强调赠品"


def test_feedback_msg_note_defaults_empty() -> None:
    from glimpse_brain.protocol import FeedbackMsg, parse_inbound

    parsed = parse_inbound(
        '{"type":"feedback","suggestion_id":"s1","region_id":"r","verdict":"up"}'
    )
    assert isinstance(parsed, FeedbackMsg)
    assert parsed.note == ""


def test_feedback_msg_rejects_bad_verdict() -> None:
    from glimpse_brain.protocol import ProtocolError, parse_inbound

    import pytest

    with pytest.raises(ProtocolError):
        parse_inbound(
            '{"type":"feedback","suggestion_id":"s1","region_id":"r","verdict":"meh"}'
        )


def test_advisory_msg_serializes_single_line() -> None:
    from glimpse_brain.protocol import AdvisoryMsg, to_line

    line = to_line(AdvisoryMsg(text="满意率已达标"))
    assert line.endswith("\n")
    assert line.count("\n") == 1
    assert '"type":"advisory"' in line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && python -m pytest tests/test_protocol.py -k "feedback or advisory" -v`
Expected: FAIL with `ImportError` / `cannot import name 'FeedbackMsg'`.

- [ ] **Step 3: Add the message classes and extend the unions**

In `brain/src/glimpse_brain/protocol.py`, add these classes after `RepliedMsg` (before `SummarizeRequest`):

```python
class FeedbackMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["feedback"] = "feedback"
    suggestion_id: str
    region_id: str
    verdict: Literal["up", "down"]
    note: str = ""  # free-text correction; meaningful with "down"
```

Add this class after `SummaryMsg`:

```python
class AdvisoryMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["advisory"] = "advisory"
    text: str
```

Update the two unions:

```python
InboundMsg = (
    OcrMsg | HelloMsg | CopiedMsg | ClickMsg | SummarizeRequest | RepliedMsg | FeedbackMsg
)
OutboundMsg = AckMsg | SuggestionsMsg | StatusMsg | SummaryMsg | AdvisoryMsg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && python -m pytest tests/test_protocol.py -k "feedback or advisory" -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/src/glimpse_brain/protocol.py brain/tests/test_protocol.py
git commit -m "feat(brain): FeedbackMsg + AdvisoryMsg wire types"
```

---

## Task 2: Config — feedback_log + FeedbackCfg

**Files:**
- Modify: `brain/src/glimpse_brain/config.py`
- Test: `brain/tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Add to `brain/tests/test_config.py` (create the file if it does not exist, with `from pathlib import Path` and `from glimpse_brain.config import Config, load_config`):

```python
def test_feedback_log_default_expands_home() -> None:
    from glimpse_brain.config import Config

    cfg = Config()
    assert cfg.brain.feedback_log.endswith("/.glimpse/feedback.jsonl")
    assert "~" not in cfg.brain.feedback_log


def test_feedback_cfg_defaults() -> None:
    from glimpse_brain.config import Config

    cfg = Config()
    assert cfg.feedback.satisfaction_window == 20
    assert cfg.feedback.advisory_threshold == 0.90
    assert cfg.feedback.advisory_min_ratings == 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && python -m pytest tests/test_config.py -k feedback -v`
Expected: FAIL with `AttributeError: 'BrainCfg' object has no attribute 'feedback_log'`.

- [ ] **Step 3: Add the config fields**

In `brain/src/glimpse_brain/config.py`, edit `BrainCfg` to add the field and extend the validator:

```python
class BrainCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    socket_path: str = Field(default_factory=lambda: str(Path("~/.glimpse/glimpse.sock").expanduser()))
    event_log: str = Field(default_factory=lambda: str(Path("~/.glimpse/events.jsonl").expanduser()))
    playbook: str = Field(default_factory=lambda: str(Path("~/.glimpse/playbook.md").expanduser()))
    feedback_log: str = Field(default_factory=lambda: str(Path("~/.glimpse/feedback.jsonl").expanduser()))

    @field_validator("socket_path", "event_log", "playbook", "feedback_log", mode="before")
    @classmethod
    def _expand(cls, v: str) -> str:
        return str(Path(v).expanduser())
```

Add a new model after `MemoryCfg`:

```python
class FeedbackCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    satisfaction_window: int = Field(default=20, ge=1)
    advisory_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    advisory_min_ratings: int = Field(default=20, ge=1)
```

Add the field to `Config`:

```python
class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brain: BrainCfg = Field(default_factory=BrainCfg)
    llm: LlmCfg = Field(default_factory=LlmCfg)
    tracker: TrackerCfg = Field(default_factory=TrackerCfg)
    redaction: RedactionCfg = Field(default_factory=RedactionCfg)
    memory: MemoryCfg = Field(default_factory=MemoryCfg)
    feedback: FeedbackCfg = Field(default_factory=FeedbackCfg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && python -m pytest tests/test_config.py -k feedback -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/src/glimpse_brain/config.py brain/tests/test_config.py
git commit -m "feat(brain): feedback_log path + FeedbackCfg"
```

---

## Task 3: SatisfactionTracker (pure rolling-rate core)

**Files:**
- Create: `brain/src/glimpse_brain/satisfaction.py`
- Test: `brain/tests/test_satisfaction.py`

- [ ] **Step 1: Write the failing tests**

Create `brain/tests/test_satisfaction.py`:

```python
from __future__ import annotations

from glimpse_brain.satisfaction import SatisfactionTracker


def make(window: int = 5, threshold: float = 1.0, min_ratings: int = 3) -> SatisfactionTracker:
    return SatisfactionTracker(window=window, threshold=threshold, min_ratings=min_ratings)


def test_below_min_ratings_never_ready() -> None:
    # 100% rate but too few samples → no advice off thin evidence.
    t = make(min_ratings=3)
    assert t.record("up") is False
    assert t.record("up") is False
    assert t.ready() is False


def test_rising_edge_fires_once_then_is_quiet() -> None:
    # Crossing into ready fires once; further 👍s do not nag.
    t = make(min_ratings=3, threshold=1.0)
    t.record("up")
    t.record("up")
    assert t.record("up") is True   # rising edge
    assert t.record("up") is False  # still ready → quiet


def test_drop_then_rise_refires() -> None:
    t = make(window=5, min_ratings=3, threshold=0.8)
    for _ in range(3):
        t.record("up")
    assert t.record("up") is True          # ready, fired
    assert t.record("down") is False       # 4up/1down=0.8 still ready, quiet
    t.record("down")                       # rate drops below 0.8 → re-arm
    assert t.ready() is False
    for _ in range(5):
        t.record("up")                     # window refills with up
    assert t._advised is True              # fired again on the new rise


def test_window_ages_out_old_verdicts() -> None:
    # A 👎 outside the window no longer counts against the rate.
    t = make(window=3, min_ratings=3, threshold=1.0)
    t.record("down")
    t.record("up")
    t.record("up")
    t.record("up")  # evicts the initial down; window is now up,up,up
    assert t.rate == 1.0
    assert t.ready() is True


def test_seed_sets_advised_to_ready() -> None:
    # Relaunch while already qualified must not re-pop the advisory.
    t = make(min_ratings=3, threshold=1.0)
    t.seed(["up", "up", "up"])
    assert t.ready() is True
    assert t.record("up") is False  # already advised from seed → no re-fire
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && python -m pytest tests/test_satisfaction.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'glimpse_brain.satisfaction'`.

- [ ] **Step 3: Implement the tracker**

Create `brain/src/glimpse_brain/satisfaction.py`:

```python
"""Pure rolling-rate satisfaction metric. Drives an advisory only — never an
action. Isolated and exhaustively tested, mirroring how Phase 3 isolated safety."""

from __future__ import annotations

from collections import deque


class SatisfactionTracker:
    def __init__(self, *, window: int, threshold: float, min_ratings: int) -> None:
        self._verdicts: deque[str] = deque(maxlen=window)
        self._threshold = threshold
        self._min_ratings = min_ratings
        self._advised = False

    @property
    def rate(self) -> float:
        if not self._verdicts:
            return 0.0
        ups = sum(1 for v in self._verdicts if v == "up")
        return ups / len(self._verdicts)

    def ready(self) -> bool:
        return len(self._verdicts) >= self._min_ratings and self.rate >= self._threshold

    def record(self, verdict: str) -> bool:
        """Append a verdict; return True only on the rising edge into ready
        (fires once, then stays quiet until the rate drops and rises again)."""
        self._verdicts.append(verdict)
        if self.ready():
            if not self._advised:
                self._advised = True
                return True
            return False
        self._advised = False
        return False

    def seed(self, verdicts: list[str]) -> None:
        """Fill from replayed history; a maxlen deque keeps only the last window.
        Set advised=ready() so a relaunch while qualified does not re-advise."""
        self._verdicts.extend(verdicts)
        self._advised = self.ready()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && python -m pytest tests/test_satisfaction.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/src/glimpse_brain/satisfaction.py brain/tests/test_satisfaction.py
git commit -m "feat(brain): SatisfactionTracker rolling-rate advisory core"
```

---

## Task 4: FeedbackRecord + FeedbackLog (redact-on-write corpus)

**Files:**
- Create: `brain/src/glimpse_brain/feedback.py`
- Test: `brain/tests/test_feedback.py`

- [ ] **Step 1: Write the failing tests**

Create `brain/tests/test_feedback.py`:

```python
from __future__ import annotations

from pathlib import Path

from glimpse_brain.feedback import FeedbackLog, FeedbackRecord
from glimpse_brain.redaction import Redactor


def make_log(tmp_path: Path) -> FeedbackLog:
    # Redact 11-digit phone numbers (same default family as the brain).
    return FeedbackLog(tmp_path / "sub" / "feedback.jsonl", Redactor([r"1[3-9]\d{9}"]))


def record(note: str = "强调赠品", conversation: list[str] | None = None) -> FeedbackRecord:
    return FeedbackRecord(
        ts="2026-06-16T00:00:00Z",
        suggestion_id="s1",
        region_id="region-1",
        verdict="down",
        note=note,
        conversation=conversation if conversation is not None else ["客户: 能便宜点吗"],
        draft="不能便宜",
        customer="老王",
    )


def test_append_then_read_round_trips(tmp_path: Path) -> None:
    log = make_log(tmp_path)  # missing parent dir is created
    log.append(record())
    out = log.read()
    assert len(out) == 1
    assert out[0].note == "强调赠品"
    assert out[0].verdict == "down"
    assert out[0].customer == "老王"


def test_conversation_and_note_redacted_on_write(tmp_path: Path) -> None:
    log = make_log(tmp_path)
    log.append(record(note="打给 13800001111", conversation=["客户: 13800001111 在吗"]))
    out = log.read()
    assert "13800001111" not in out[0].note
    assert "13800001111" not in out[0].conversation[0]


def test_read_missing_file_is_empty(tmp_path: Path) -> None:
    log = FeedbackLog(tmp_path / "nope.jsonl", Redactor([]))
    assert log.read() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && python -m pytest tests/test_feedback.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'glimpse_brain.feedback'`.

- [ ] **Step 3: Implement the corpus**

Create `brain/src/glimpse_brain/feedback.py`:

```python
"""Durable feedback corpus: the raw material the offline distiller reads. Every
field that can carry PII is redacted on write (customer is the memory join key,
already stored raw by the memory subsystem, so it is kept as-is here)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from glimpse_brain.redaction import Redactor

log = logging.getLogger("glimpse.feedback")


@dataclass(frozen=True)
class FeedbackRecord:
    ts: str
    suggestion_id: str
    region_id: str
    verdict: str
    note: str
    conversation: list[str]
    draft: str
    customer: str


class FeedbackLog:
    def __init__(self, path: Path, redactor: Redactor) -> None:
        self._path = path
        self._redactor = redactor
        path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: FeedbackRecord) -> None:
        redacted = FeedbackRecord(
            ts=record.ts,
            suggestion_id=record.suggestion_id,
            region_id=record.region_id,
            verdict=record.verdict,
            note=self._redactor.redact(record.note),
            conversation=[self._redactor.redact(line) for line in record.conversation],
            draft=self._redactor.redact(record.draft),
            customer=record.customer,
        )
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(redacted), ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError:  # a disk failure must not break the feedback path
            log.warning("feedback corpus append failed", exc_info=True)

    def read(self) -> list[FeedbackRecord]:
        if not self._path.exists():
            return []
        records: list[FeedbackRecord] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(FeedbackRecord(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue  # skip a corrupt line, keep the rest
        return records
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && python -m pytest tests/test_feedback.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/src/glimpse_brain/feedback.py brain/tests/test_feedback.py
git commit -m "feat(brain): FeedbackRecord + redact-on-write FeedbackLog corpus"
```

---

## Task 5: Server wiring — snapshot cache, _on_feedback, seeding, advisory

**Files:**
- Modify: `brain/src/glimpse_brain/server.py`
- Test: `brain/tests/test_server.py`

**Scene:** `GlimpseServer.__init__` builds the agent and memory; `_dispatch` routes inbound messages; `_fire` runs the agent and sends `SuggestionsMsg`; `_on_ocr` sets `self._current_customer`. Feedback must capture the conversation+draft that prompted a suggestion, so `_fire` records a per-region snapshot.

- [ ] **Step 1: Write the failing tests**

First, extend `make_config` in `brain/tests/test_server.py` so the brain dir is the tmp dir. Replace the `"brain"` block inside `make_config` with:

```python
            "brain": {
                "socket_path": str(tmp_path / "glimpse.sock"),
                "event_log": str(tmp_path / "events.jsonl"),
                "playbook": str(playbook),
                "feedback_log": str(tmp_path / "feedback.jsonl"),
            },
```

Then add these tests at the end of `brain/tests/test_server.py`:

```python
async def test_feedback_writes_corpus_event_and_memory(tmp_path: Path) -> None:
    from glimpse_brain.memory import InMemoryMemory

    cfg = make_config(tmp_path)
    memory = InMemoryMemory()
    server = GlimpseServer(
        cfg, llm=FakeLLM(), tool_client=FakeToolClient(), memory=memory
    )
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(b'{"type":"hello","shell_version":"0.1.0"}\n')
        await writer.drain()
        await read_until(reader, "status")

        # An OCR with a contact sets the current customer AND fires a suggestion,
        # so a snapshot (conversation + draft) exists for the feedback to resolve.
        ocr = (
            '{"type":"ocr","seq":1,"ts":"2026-06-11T12:00:00Z","region_id":"region-1",'
            '"contact":"老王",'
            '"blocks":[{"text":"能便宜点吗","x0":0.05,"x1":0.4,"conf":0.95}]}\n'
        )
        writer.write(ocr.encode())
        await writer.drain()
        sug = await read_until(reader, "suggestions")
        sid = sug["items"][0]["id"]

        writer.write(
            (
                '{"type":"feedback","suggestion_id":"%s","region_id":"region-1",'
                '"verdict":"down","note":"强调赠品"}\n' % sid
            ).encode()
        )
        await writer.drain()
        await asyncio.sleep(0.1)

        # Corpus written with the draft + conversation snapshot.
        corpus = (tmp_path / "feedback.jsonl").read_text(encoding="utf-8").strip()
        assert "强调赠品" in corpus
        assert "能便宜点吗" in corpus
        # Audit event present (note redacted by redact_payload like all events).
        events = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
        assert '"kind": "feedback"' in events
        # Correction written to the customer's memory.
        hits = await memory.recall("老王", "修正", k=5)
        assert any("强调赠品" in h.text for h in hits)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_satisfaction_advisory_fires_on_threshold(tmp_path: Path) -> None:
    cfg = Config.model_validate(
        {
            "brain": {
                "socket_path": str(tmp_path / "glimpse.sock"),
                "event_log": str(tmp_path / "events.jsonl"),
                "playbook": str(tmp_path / "pb.md"),
                "feedback_log": str(tmp_path / "feedback.jsonl"),
            },
            "tracker": {"settle_ms": 30},
            "memory": {"enabled": False},
            "feedback": {
                "satisfaction_window": 5,
                "advisory_threshold": 1.0,
                "advisory_min_ratings": 3,
            },
        }
    )
    (tmp_path / "pb.md").write_text("满99包邮", encoding="utf-8")
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=FakeToolClient())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(b'{"type":"hello","shell_version":"0.1.0"}\n')
        await writer.drain()
        await read_until(reader, "status")
        for _ in range(3):
            writer.write(
                b'{"type":"feedback","suggestion_id":"s1","region_id":"r",'
                b'"verdict":"up","note":""}\n'
            )
            await writer.drain()
        adv = await read_until(reader, "advisory")
        assert "自动发送" in adv["text"]
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && python -m pytest tests/test_server.py -k "feedback or advisory" -v`
Expected: FAIL — the `feedback` message is ignored (no advisory/corpus), so `read_until` times out / asserts fail.

- [ ] **Step 3: Wire the server**

In `brain/src/glimpse_brain/server.py`:

(a) Add `import json` to the imports at the top (after `import asyncio`).

(b) Extend the protocol import to include the two new messages:

```python
from glimpse_brain.protocol import (
    AckMsg,
    AdvisoryMsg,
    ClickMsg,
    CopiedMsg,
    FeedbackMsg,
    HelloMsg,
    OcrMsg,
    OutboundMsg,
    ProtocolError,
    RepliedMsg,
    StatusMsg,
    SuggestionItem,
    SuggestionsMsg,
    SummarizeRequest,
    SummaryMsg,
    parse_inbound,
    to_line,
)
```

(c) Add the new unit imports near the other `glimpse_brain` imports:

```python
from glimpse_brain.feedback import FeedbackLog, FeedbackRecord
from glimpse_brain.satisfaction import SatisfactionTracker
```

(d) In `__init__`, after `self._last_image: str = ""`, add:

```python
        self._feedback_log = FeedbackLog(
            Path(cfg.brain.feedback_log), self._redactor
        )
        self._satisfaction = SatisfactionTracker(
            window=cfg.feedback.satisfaction_window,
            threshold=cfg.feedback.advisory_threshold,
            min_ratings=cfg.feedback.advisory_min_ratings,
        )
        # region_id -> {"tail": [...], "items": {suggestion_id: text}} captured at
        # suggestion time, so feedback resolves the exact draft + conversation.
        self._last_suggestions: dict[str, dict] = {}
```

At the end of `__init__` (after `self._summarizing = False`), add:

```python
        self._seed_satisfaction()
```

(e) Add a dispatch branch in `_dispatch`, after the `RepliedMsg` branch:

```python
        elif isinstance(msg, FeedbackMsg):
            await self._on_feedback(msg)
```

(f) In `_fire`, capture the tail and store the snapshot. Replace the opening of `_fire` up to the `items = [...]` block with:

```python
    async def _fire(self) -> None:
        tail = self._tracker.tail()
        try:
            result = await self._agent.suggest(
                tail,
                customer=self._current_customer,
                image=self._last_image or None,
            )
        except CostCapExceeded:
            await self._send(StatusMsg(state="degraded", detail="cost cap reached"))
            return
        except SuggestionParseError:
            await self._send(StatusMsg(state="degraded", detail="unusable LLM output"))
            return
        except Exception as exc:  # LLM/network failure must not kill the loop
            log.exception("suggestion pass failed")
            self._events.append("error", self._region_id, {"error": str(exc)[:200]})
            await self._send(StatusMsg(state="degraded", detail="llm error"))
            return
        self._events.append(
            "agent_turn",
            self._region_id,
            {"tools_used": result.tools_used, "draft_count": len(result.drafts)},
        )
        items = [
            SuggestionItem(id=f"s{i}", text=text)
            for i, text in enumerate(result.drafts, 1)
        ]
        self._last_suggestions[self._region_id] = {
            "tail": tail,
            "items": {it.id: it.text for it in items},
        }
```

(Leave the rest of `_fire` — the `suggestion_shown` event and the two `_send` calls — unchanged.)

(g) Add the two new methods after `_fire`:

```python
    async def _on_feedback(self, msg: FeedbackMsg) -> None:
        # Audit event (note redacted by redact_payload, like every event).
        self._events.append(
            "feedback",
            msg.region_id,
            {"suggestion_id": msg.suggestion_id, "verdict": msg.verdict, "note": msg.note},
        )
        snapshot = self._last_suggestions.get(msg.region_id, {})
        conversation = list(snapshot.get("tail", []))
        draft = snapshot.get("items", {}).get(msg.suggestion_id, "")
        self._feedback_log.append(
            FeedbackRecord(
                ts=datetime.now(UTC).isoformat(),
                suggestion_id=msg.suggestion_id,
                region_id=msg.region_id,
                verdict=msg.verdict,
                note=msg.note,
                conversation=conversation,
                draft=draft,
                customer=self._current_customer or "",
            )
        )
        # A correction with a known customer becomes recallable memory.
        if (
            msg.verdict == "down"
            and msg.note
            and self._memory is not None
            and self._current_customer
        ):
            try:
                await self._memory.write(
                    self._current_customer,
                    self._redactor.redact(f"（人工修正）{msg.note}"),
                    "correction",
                )
            except Exception:  # memory failure must not break feedback
                log.exception("feedback memory write failed")
        if self._satisfaction.record(msg.verdict):
            await self._send(AdvisoryMsg(text="满意率已达标，可考虑开启自动发送"))

    def _seed_satisfaction(self) -> None:
        path = Path(self._cfg.brain.event_log)
        if not path.exists():
            return
        verdicts: list[str] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("kind") == "feedback":
                    verdict = rec.get("payload", {}).get("verdict")
                    if verdict in ("up", "down"):
                        verdicts.append(verdict)
        except OSError:
            return
        self._satisfaction.seed(verdicts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && python -m pytest tests/test_server.py -k "feedback or advisory" -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full brain suite**

Run: `cd brain && python -m pytest -q`
Expected: all pass (the pre-existing deselected integration test stays deselected).

- [ ] **Step 6: Commit**

```bash
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/src/glimpse_brain/server.py brain/tests/test_server.py
git commit -m "feat(brain): capture feedback → corpus + memory + advisory, seed metric at startup"
```

---

## Task 6: Distiller (pure record → candidate eval case)

**Files:**
- Create: `brain/src/glimpse_brain/evals_pkg/distill.py`
- Test: `brain/tests/test_distill.py`

- [ ] **Step 1: Write the failing tests**

Create `brain/tests/test_distill.py`:

```python
from __future__ import annotations

from glimpse_brain.evals_pkg.distill import (
    candidate_id,
    record_to_prompt,
    response_to_case,
)
from glimpse_brain.feedback import FeedbackRecord


def record(note: str = "强调赠品/包邮，不要直接降价") -> FeedbackRecord:
    return FeedbackRecord(
        ts="2026-06-16T00:00:00Z",
        suggestion_id="s1",
        region_id="r",
        verdict="down",
        note=note,
        conversation=["客户: 能便宜点吗"],
        draft="不能便宜",
        customer="老王",
    )


def test_candidate_id_is_deterministic_and_prefixed() -> None:
    a = candidate_id(record())
    b = candidate_id(record())
    assert a == b
    assert a.startswith("fb-")
    assert candidate_id(record(note="别的修正")) != a


def test_record_to_prompt_includes_all_three_parts() -> None:
    prompt = record_to_prompt(record())
    assert "能便宜点吗" in prompt   # conversation
    assert "不能便宜" in prompt     # rejected draft
    assert "强调赠品" in prompt     # human correction


def test_response_to_case_builds_valid_schema() -> None:
    raw = '解释一下。{"rubric_focus":["grounded"],"must":["赠品|包邮"],"must_not":["降价"],"notes":"议价"}'
    case = response_to_case(raw, "fb-abc123", ["客户: 能便宜点吗"])
    assert case["id"] == "fb-abc123"
    assert case["conversation"] == ["客户: 能便宜点吗"]
    assert case["must"] == ["赠品|包邮"]
    assert case["must_not"] == ["降价"]
    assert case["rubric_focus"] == ["grounded"]
    assert case["notes"] == "议价"


def test_response_to_case_fails_closed_on_garbage() -> None:
    # No JSON object → empty constraint lists, never a crash.
    case = response_to_case("the model rambled with no json", "fb-x", ["客户: hi"])
    assert case["id"] == "fb-x"
    assert case["must"] == []
    assert case["must_not"] == []
    assert case["rubric_focus"] == []
    assert case["notes"] == ""


def test_response_to_case_loaded_by_harness() -> None:
    # A distilled case must round-trip through the real eval loader.
    import json
    from pathlib import Path
    import tempfile

    from glimpse_brain.evals_pkg.harness import load_cases

    case = response_to_case(
        '{"rubric_focus":["tone"],"must":["赠品"],"must_not":[],"notes":"n"}',
        "fb-deadbeef",
        ["客户: 能便宜点吗"],
    )
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "fb-deadbeef.json").write_text(
            json.dumps(case, ensure_ascii=False), encoding="utf-8"
        )
        loaded = load_cases(Path(d))
    assert len(loaded) == 1
    assert loaded[0].id == "fb-deadbeef"
    assert loaded[0].must == ["赠品"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && python -m pytest tests/test_distill.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'glimpse_brain.evals_pkg.distill'`.

- [ ] **Step 3: Implement the distiller**

Create `brain/src/glimpse_brain/evals_pkg/distill.py`:

```python
"""Pure distillation: turn one human correction into a candidate eval-case dict.
The model call lives in the offline CLI (evals/__main__.py); this module only
builds the prompt and parses the response, so it is testable without the API.
The `conversation` field comes from the record (the real exchange), never the
model — only rubric_focus/must/must_not/notes are model-authored."""

from __future__ import annotations

import hashlib
import json

from glimpse_brain.feedback import FeedbackRecord

DISTILL_SYSTEM = """\
你是一名客服质量评审，把一条人工修正提炼成一个回归测试用例。
给定：对话、被否决的 AI 草稿、人工修正建议。
只输出一个 JSON 对象，键如下，不要输出任何其他内容：
- rubric_focus: 字符串数组，取自 grounded/tone/handles_uncertainty/safe
- must: 正则字符串数组，合格回复至少应命中其一（可为空）
- must_not: 正则字符串数组，合格回复不得命中任何一个（可为空）
- notes: 一句话说明这条用例考察什么"""


def candidate_id(record: FeedbackRecord) -> str:
    key = "\x00".join([*record.conversation, record.draft, record.note])
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    return f"fb-{digest}"


def record_to_prompt(record: FeedbackRecord) -> str:
    return (
        "对话：\n"
        + "\n".join(record.conversation)
        + "\n\n被否决的 AI 草稿：\n"
        + record.draft
        + "\n\n人工修正建议：\n"
        + record.note
    )


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def response_to_case(raw: str, case_id: str, conversation: list[str]) -> dict:
    decoder = json.JSONDecoder()
    body: dict = {}
    for i, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(raw[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            body = candidate
            break
    notes = body.get("notes", "")
    return {
        "id": case_id,
        "conversation": conversation,
        "rubric_focus": _strings(body.get("rubric_focus")),
        "must": _strings(body.get("must")),
        "must_not": _strings(body.get("must_not")),
        "notes": notes if isinstance(notes, str) else "",
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && python -m pytest tests/test_distill.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/src/glimpse_brain/evals_pkg/distill.py brain/tests/test_distill.py
git commit -m "feat(evals): pure correction→candidate-eval-case distiller"
```

---

## Task 7: Eval CLI — distill + promote subcommands, gitignore candidates

**Files:**
- Modify: `brain/evals/__main__.py`
- Modify: `.gitignore`
- Test: manual (the subcommands make real API calls / move files); add a non-API unit test for `promote`.

**Scene:** `brain/evals/__main__.py` currently runs every golden case on `python -m evals`. `CASES_DIR = Path(__file__).parent / "cases"` and `load_cases(CASES_DIR)` globs `*.json` **non-recursively**, so a `cases/candidates/` subdir is never part of the gating run. We add two subcommands without disturbing the default run.

- [ ] **Step 1: Add the gitignore entry**

Append to `.gitignore`:

```
# Machine-generated eval candidates (promote into cases/ to gate on them)
brain/evals/cases/candidates/
```

- [ ] **Step 2: Write the failing test for promote**

Create `brain/tests/test_evals_cli.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import importlib


def test_promote_moves_candidate_into_gating_dir(tmp_path: Path, monkeypatch) -> None:
    mod = importlib.import_module("evals.__main__")
    cases = tmp_path / "cases"
    (cases / "candidates").mkdir(parents=True)
    body = {"id": "fb-abc", "conversation": ["客户: hi"], "rubric_focus": [],
            "must": [], "must_not": [], "notes": "n"}
    (cases / "candidates" / "fb-abc.json").write_text(
        json.dumps(body, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(mod, "CASES_DIR", cases)

    mod.promote("fb-abc")

    assert (cases / "fb-abc.json").exists()
    assert not (cases / "candidates" / "fb-abc.json").exists()
```

Note: running this requires `brain/` on `sys.path` (the eval package is imported as `evals`). The brain test run is invoked from `brain/` with `PYTHONPATH` including the repo's eval root. Confirm by running from `brain/`:

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd brain && PYTHONPATH=.:$PYTHONPATH python -m pytest tests/test_evals_cli.py -v`
Expected: FAIL with `AttributeError: module 'evals.__main__' has no attribute 'promote'`.

- [ ] **Step 4: Add the subcommands**

Edit `brain/evals/__main__.py`. Add imports near the top (after the existing imports):

```python
import sys

from glimpse_brain.evals_pkg.distill import (
    candidate_id,
    record_to_prompt,
    response_to_case,
    DISTILL_SYSTEM,
)
from glimpse_brain.feedback import FeedbackLog
```

Rename the existing `_main` to `_run` (leave its body unchanged) and add the new functions plus a dispatcher. Replace the `if __name__ == "__main__":` block at the bottom with:

```python
async def _distill() -> None:
    cfg = load_config(Path("~/.glimpse/glimpse.toml").expanduser())
    redactor = Redactor(cfg.redaction.patterns)
    corpus = FeedbackLog(Path(cfg.brain.feedback_log), redactor)
    client = AnthropicLLM()
    candidates_dir = CASES_DIR / "candidates"
    candidates_dir.mkdir(parents=True, exist_ok=True)
    seen = {p.stem for p in CASES_DIR.glob("*.json")} | {
        p.stem for p in candidates_dir.glob("*.json")
    }
    for record in corpus.read():
        if record.verdict != "down" or not record.note:
            continue
        cid = candidate_id(record)
        if cid in seen:
            continue
        try:
            raw = await client.complete(
                system=DISTILL_SYSTEM,
                user=record_to_prompt(record),
                model=cfg.llm.model,
            )
        except Exception as exc:  # per-record fail-soft: skip, keep going
            print(f"[skip] {cid}: {exc}")
            continue
        case = response_to_case(raw, cid, record.conversation)
        (candidates_dir / f"{cid}.json").write_text(
            json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        seen.add(cid)
        print(f"[candidate] {cid}  must={case['must']}  must_not={case['must_not']}")


def promote(case_id: str) -> None:
    src = CASES_DIR / "candidates" / f"{case_id}.json"
    dst = CASES_DIR / f"{case_id}.json"
    if not src.exists():
        print(f"no candidate: {case_id}")
        return
    src.rename(dst)
    print(f"promoted {case_id} → {dst}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        asyncio.run(_run())
    elif args[0] == "distill":
        asyncio.run(_distill())
    elif args[0] == "promote" and len(args) == 2:
        promote(args[1])
    else:
        print("usage: python -m evals [distill | promote <id>]")
        raise SystemExit(2)
```

- [ ] **Step 5: Run the promote test to verify it passes**

Run: `cd brain && PYTHONPATH=.:$PYTHONPATH python -m pytest tests/test_evals_cli.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Verify the default eval run still parses (no API call needed for arg-routing)**

Run: `cd brain && PYTHONPATH=.:$PYTHONPATH python -m evals badcmd`
Expected: prints `usage: python -m evals [distill | promote <id>]` and exits non-zero.

- [ ] **Step 7: Commit**

```bash
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/evals/__main__.py brain/tests/test_evals_cli.py .gitignore
git commit -m "feat(evals): distill + promote subcommands; candidates dir is non-gating"
```

---

## Task 8: Shell protocol — FeedbackMsg encode + AdvisoryMsg decode

**Files:**
- Modify: `shell/Sources/GlimpseShellLib/Protocol.swift`
- Test: `shell/Tests/GlimpseShellTests/ProtocolTests.swift` (add cases; if the file does not exist, create it with `import Testing`, `import Foundation`, `@testable import GlimpseShellLib`)

- [ ] **Step 1: Write the failing tests**

Add to `shell/Tests/GlimpseShellTests/ProtocolTests.swift`:

```swift
@Test("FeedbackMsg encodes snake_case keys")
func feedbackMsgEncodesSnakeCase() throws {
    let data = try Wire.encodeLine(
        FeedbackMsg(suggestionId: "s1", regionId: "r", verdict: "down", note: "强调赠品")
    )
    let line = String(decoding: data, as: UTF8.self)
    #expect(line.contains("\"suggestion_id\":\"s1\""))
    #expect(line.contains("\"region_id\":\"r\""))
    #expect(line.contains("\"verdict\":\"down\""))
    #expect(line.contains("\"type\":\"feedback\""))
}

@Test("AdvisoryMsg decodes via Wire")
func advisoryMsgDecodes() throws {
    let line = Data("{\"type\":\"advisory\",\"text\":\"满意率已达标\"}".utf8)
    guard case .advisory(let msg)? = Wire.decodeBrainMessage(line) else {
        Issue.record("expected .advisory")
        return
    }
    #expect(msg.text == "满意率已达标")
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd shell && swift test --filter ProtocolTests 2>&1 | tail -20`
Expected: FAIL to build — `cannot find 'FeedbackMsg' in scope` / no `.advisory` case.

- [ ] **Step 3: Add the Swift types**

In `shell/Sources/GlimpseShellLib/Protocol.swift`, add after `RepliedMsg` (before `ClickMsg`):

```swift
public struct FeedbackMsg: Codable {
    public var type = "feedback"
    public var suggestionId: String
    public var regionId: String
    public var verdict: String
    public var note: String

    public init(suggestionId: String, regionId: String, verdict: String, note: String = "") {
        self.suggestionId = suggestionId
        self.regionId = regionId
        self.verdict = verdict
        self.note = note
    }

    enum CodingKeys: String, CodingKey {
        case type
        case suggestionId = "suggestion_id"
        case regionId = "region_id"
        case verdict, note
    }
}
```

Add after `SummaryMsg`:

```swift
public struct AdvisoryMsg: Codable {
    // Decode-only: the wire "type" key is consumed by Wire.decodeBrainMessage's probe.
    public var text: String

    public init(text: String) {
        self.text = text
    }
}
```

Extend the `BrainMessage` enum:

```swift
public enum BrainMessage {
    case ack(AckMsg)
    case suggestions(SuggestionsMsg)
    case status(StatusMsg)
    case summary(SummaryMsg)
    case advisory(AdvisoryMsg)
}
```

Add a case to the `switch probe.type` in `decodeBrainMessage`, after the `"summary"` case:

```swift
        case "advisory":
            return (try? decoder.decode(AdvisoryMsg.self, from: line)).map(BrainMessage.advisory)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd shell && swift test --filter ProtocolTests 2>&1 | tail -20`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/john/Projects/visual-habit-tracking-agent
git add shell/Sources/GlimpseShellLib/Protocol.swift shell/Tests/GlimpseShellTests/ProtocolTests.swift
git commit -m "feat(shell): FeedbackMsg encode + AdvisoryMsg decode"
```

---

## Task 9: Overlay UI — 👍/👎 + correction note + advisory line

**Files:**
- Modify: `shell/Sources/GlimpseShellLib/Overlay.swift`
- Test: none (overlay UI is the deliberate manual E2E seam, consistent with Phase 3). Must compile.

- [ ] **Step 1: Add model state + controller method**

In `OverlayModel`, after `public var onAct: ...`, add:

```swift
    @Published public var advisory: String = ""
    public var onFeedback: ((_ suggestionId: String, _ verdict: String, _ note: String) -> Void)?
```

In `OverlayController`, add after `showContact`:

```swift
    /// Safe to call from any thread: @Published mutation hops to main inside.
    public func showAdvisory(_ text: String) {
        DispatchQueue.main.async { self.model.advisory = text }
    }
```

- [ ] **Step 2: Add the advisory line to the view**

In `OverlayView.body`, after the `if !model.summary.isEmpty { ... }` block and before `if model.items.isEmpty`, add:

```swift
            if !model.advisory.isEmpty {
                HStack(alignment: .top, spacing: 6) {
                    Text("💡 \(model.advisory)")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer()
                    Button("知道了") { model.advisory = "" }
                        .font(.caption2)
                }
                .padding(8)
                .background(Color.yellow.opacity(0.12))
                .cornerRadius(6)
            }
```

- [ ] **Step 3: Replace the per-item card with a feedback-capable subview**

In `OverlayView.body`, replace the `ForEach(model.items) { item in ... }` block with:

```swift
                    ForEach(model.items) { item in
                        SuggestionCard(item: item, model: model)
                    }
```

Add this new view at the end of the file (after the `OverlayView` struct):

```swift
struct SuggestionCard: View {
    let item: SuggestionItem
    @ObservedObject var model: OverlayModel
    @State private var showNote = false
    @State private var note = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 8) {
                Text(item.text)
                    .font(.system(size: 13))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button("复制") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(item.text, forType: .string)
                    model.onCopy?(item.id)
                }
                Button(model.autoSendOn && !model.stale ? "发送" : "填入") {
                    model.onAct?(item.id, item.text)
                }
            }
            HStack(spacing: 10) {
                Button("👍") { model.onFeedback?(item.id, "up", "") }
                    .buttonStyle(.plain)
                Button("👎") { showNote.toggle() }
                    .buttonStyle(.plain)
                Spacer()
            }
            .font(.system(size: 13))
            if showNote {
                HStack(spacing: 6) {
                    TextField("更好的回复 / 修改建议", text: $note)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 12))
                    Button("提交") {
                        model.onFeedback?(item.id, "down", note)
                        note = ""
                        showNote = false
                    }
                    .font(.caption)
                }
            }
        }
        .padding(8)
        .background(Color.gray.opacity(0.12))
        .cornerRadius(6)
    }
}
```

- [ ] **Step 4: Build to verify it compiles**

Run: `cd shell && swift build 2>&1 | tail -20`
Expected: `Build complete!` (no errors).

- [ ] **Step 5: Commit**

```bash
cd /Users/john/Projects/visual-habit-tracking-agent
git add shell/Sources/GlimpseShellLib/Overlay.swift
git commit -m "feat(shell): per-suggestion 👍/👎 + correction note + advisory line"
```

---

## Task 10: Wire the overlay to IPC in main.swift

**Files:**
- Modify: `shell/Sources/GlimpseShell/main.swift`
- Test: none (integration seam; verified by build + manual E2E).

**Scene:** `main.swift` sets `overlay.model.onCopy` / `overlay.model.onAct` to `ipc.send(...)` and switches over `BrainMessage` in `handleBrainMessage`. We add the feedback send and the advisory handling, mirroring those patterns.

- [ ] **Step 1: Wire onFeedback near the other overlay callbacks**

In `main.swift`, after the `overlay.model.onAct = { ... }` closure (around line 119-135), add:

```swift
        overlay.model.onFeedback = { [weak self] id, verdict, note in
            guard let self else { return }
            self.ipc.send(FeedbackMsg(suggestionId: id, regionId: self.regionId, verdict: verdict, note: note))
        }
```

- [ ] **Step 2: Handle the advisory message**

In the `BrainMessage` switch (around line 290-295), add after the `.summary` case:

```swift
        case .advisory(let msg):
            overlay.showAdvisory(msg.text)
```

- [ ] **Step 3: Build to verify it compiles**

Run: `cd shell && swift build 2>&1 | tail -20`
Expected: `Build complete!`

- [ ] **Step 4: Run the full shell suite**

Run: `cd shell && swift test 2>&1 | tail -20`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/john/Projects/visual-habit-tracking-agent
git add shell/Sources/GlimpseShell/main.swift
git commit -m "feat(shell): send FeedbackMsg, display advisory"
```

---

## Task 11: Docs — README E2E checklist + distill/promote usage

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the feedback E2E + distillation steps**

In `README.md`, find the E2E manual-checklist section (where Phase 3 / vision checks live) and add:

```markdown
### Feedback loop (manual E2E)
- [ ] A suggestion card shows 👍 / 👎 buttons.
- [ ] Tapping 👎 reveals a "更好的回复 / 修改建议" field; 提交 sends it.
- [ ] After enough 👍 (≥ advisory_min_ratings at ≥ advisory_threshold), a 💡 advisory line appears and is dismissable — and the 自动发送 toggle does NOT change.
- [ ] `~/.glimpse/feedback.jsonl` gains a line per rating (conversation + note redacted).

### Distilling feedback into eval cases (offline, billable)
```bash
cd brain
PYTHONPATH=. ANTHROPIC_API_KEY=… python -m evals distill      # corrections → cases/candidates/*.json
# review a candidate, then:
PYTHONPATH=. python -m evals promote fb-<id>                   # move it into the gating set
PYTHONPATH=. ANTHROPIC_API_KEY=… python -m evals              # run the gating set (now includes it)
```
Candidates in `cases/candidates/` are gitignored and never run by the default eval — only promoted cases gate.
```

- [ ] **Step 2: Commit**

```bash
cd /Users/john/Projects/visual-habit-tracking-agent
git add README.md
git commit -m "docs: feedback-loop E2E checklist + distill/promote usage"
```

---

## Final verification (after all tasks)

- [ ] **Brain suite green:** `cd brain && python -m pytest -q` — all pass, integration test still deselected.
- [ ] **Eval CLI test green:** `cd brain && PYTHONPATH=.:$PYTHONPATH python -m pytest tests/test_evals_cli.py -q`.
- [ ] **Shell suite green:** `cd shell && swift test 2>&1 | tail -5`.
- [ ] **Ruff clean:** `cd brain && ruff check src tests evals`.
- [ ] **No advisory→toggle path:** grep confirms the advisory only sets `model.advisory` text and never touches `autoSendOn`: `grep -n "autoSendOn" shell/Sources/GlimpseShellLib/Overlay.swift shell/Sources/GlimpseShell/main.swift` shows no assignment inside advisory handling.
```
