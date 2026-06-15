from __future__ import annotations

from pathlib import Path

from glimpse_brain.knowledge import FileKnowledgeBase


def test_grounding_wraps_playbook(tmp_path: Path) -> None:
    pb = tmp_path / "playbook.md"
    pb.write_text("满99包邮", encoding="utf-8")
    kb = FileKnowledgeBase(playbook_path=pb)
    out = kb.grounding("包邮吗")
    assert "<playbook>" in out and "满99包邮" in out


def test_grounding_includes_learnings_when_present(tmp_path: Path) -> None:
    pb = tmp_path / "playbook.md"
    pb.write_text("政策", encoding="utf-8")
    lr = tmp_path / "learnings.md"
    lr.write_text("- 议价强调赠品", encoding="utf-8")
    kb = FileKnowledgeBase(playbook_path=pb, learnings_path=lr)
    out = kb.grounding("x")
    assert "<learnings>" in out and "议价强调赠品" in out


def test_grounding_fails_soft_on_missing_playbook(tmp_path: Path) -> None:
    # WHY: a misconfigured path must never error the suggestion path.
    kb = FileKnowledgeBase(playbook_path=tmp_path / "nope.md")
    out = kb.grounding("x")
    assert "(playbook file missing)" in out
    assert "<learnings>" not in out  # absent learnings -> block omitted
