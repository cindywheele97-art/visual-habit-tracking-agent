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
