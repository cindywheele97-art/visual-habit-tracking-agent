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
