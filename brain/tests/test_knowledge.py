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
    assert out.index("shipping") < out.index("apple")  # (type, id): policy < product


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
