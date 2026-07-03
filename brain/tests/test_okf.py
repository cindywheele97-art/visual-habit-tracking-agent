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
    doc = parse_doc(write(tmp_path / "greeting.md", "亲，您好～\n第二行"))
    assert doc.id == "greeting"
    assert doc.title == "greeting"
    assert doc.type == "other"
    assert doc.description == "亲，您好～"
    assert doc.tags == []
    assert doc.body == "亲，您好～\n第二行"


def test_doc_starting_with_horizontal_rule_keeps_all_content(tmp_path: Path) -> None:
    # WHY: '---' at the top of a plain doc is a markdown horizontal rule, not a
    # frontmatter fence. Substring splitting silently drops everything between
    # the first two rules and the agent quotes a gutted document as policy.
    doc = parse_doc(write(tmp_path / "rule.md", "---\n运费规则\n---\n满99包邮"))
    assert "运费规则" in doc.body
    assert "满99包邮" in doc.body


def test_frontmatter_value_containing_triple_dash(tmp_path: Path) -> None:
    # WHY: '---' inside a frontmatter VALUE must not terminate the block early.
    doc = parse_doc(
        write(tmp_path / "a.md", "---\nid: a\ntitle: A---B\n---\n正文")
    )
    assert doc.title == "A---B"
    assert doc.body == "正文"


def test_empty_frontmatter_block_is_stripped(tmp_path: Path) -> None:
    # WHY: an empty fence pair is real (empty) frontmatter — the fences must
    # not leak into the body or become the doc's description.
    doc = parse_doc(write(tmp_path / "e.md", "---\n---\n退货政策：满99包邮"))
    assert doc.body == "退货政策：满99包邮"
    assert doc.description == "退货政策：满99包邮"


def test_comment_only_frontmatter_is_stripped(tmp_path: Path) -> None:
    doc = parse_doc(write(tmp_path / "c.md", "---\n# todo: fill in\n---\n正文"))
    assert doc.body == "正文"


def test_body_horizontal_rule_survives(tmp_path: Path) -> None:
    # WHY: a horizontal rule inside the body is legal markdown; the body must
    # arrive at the agent intact.
    doc = parse_doc(
        write(tmp_path / "b.md", "---\nid: b\n---\n上半\n\n---\n\n下半")
    )
    assert "上半" in doc.body and "下半" in doc.body


def test_load_catalog_skips_malformed_yaml(tmp_path: Path) -> None:
    write(tmp_path / "good.md", FULL)
    write(tmp_path / "bad.md", "---\ntitle: \"unterminated\ntype: [oops\n---\nbody\n")
    docs = load_catalog(tmp_path)
    ids = {d.id for d in docs}
    assert "shipping" in ids
    assert "bad" not in ids


def test_load_catalog_dedups_ids_first_wins(tmp_path: Path) -> None:
    write(tmp_path / "a.md", "---\nid: dup\ntitle: A\n---\nfirst\n")
    write(tmp_path / "b.md", "---\nid: dup\ntitle: B\n---\nsecond\n")
    docs = [d for d in load_catalog(tmp_path) if d.id == "dup"]
    assert len(docs) == 1
    assert docs[0].title == "A"


def test_load_catalog_recurses_subdirs(tmp_path: Path) -> None:
    write(tmp_path / "nested" / "deep.md", "---\nid: deep\ntype: policy\n---\nx\n")
    assert any(d.id == "deep" for d in load_catalog(tmp_path))


def test_load_catalog_back_compat_synthesizes_playbook(tmp_path: Path) -> None:
    legacy = write(tmp_path / "playbook.md", "# 客服话术\n满99包邮")
    docs = load_catalog(tmp_path / "knowledge", legacy_playbook=legacy)
    assert len(docs) == 1
    assert docs[0].id == "playbook"
    assert "满99包邮" in docs[0].body


def test_load_catalog_empty_is_empty(tmp_path: Path) -> None:
    assert load_catalog(tmp_path / "nope") == []
