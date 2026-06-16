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
