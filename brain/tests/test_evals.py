from __future__ import annotations

import json
from pathlib import Path

from glimpse_brain.evals_pkg.harness import (
    EvalCase,
    check_must_constraints,
    load_cases,
    summarize,
)


def test_load_cases_reads_json_dir(tmp_path: Path) -> None:
    (tmp_path / "c1.json").write_text(
        json.dumps({
            "id": "haggle-01",
            "conversation": ["客户: 便宜点"],
            "rubric_focus": ["grounded"],
            "must": ["赠品|包邮"],
            "must_not": ["直接.*降价"],
            "notes": "议价",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    cases = load_cases(tmp_path)
    assert len(cases) == 1
    assert isinstance(cases[0], EvalCase)
    assert cases[0].id == "haggle-01"


def test_check_must_constraints_pass_and_fail() -> None:
    case = EvalCase(
        id="x", conversation=["c"], rubric_focus=[],
        must=["赠品|包邮"], must_not=["直接.*降价"], notes="",
    )
    ok = check_must_constraints(case, ["亲，满99包邮哦"])
    assert ok.passed and ok.failures == []

    bad = check_must_constraints(case, ["可以直接给您降价"])
    assert not bad.passed
    # both a missing `must` and a present `must_not` are reported distinctly
    assert any("must-missing" in f for f in bad.failures)
    assert any("must_not-present" in f for f in bad.failures)


def test_must_pattern_does_not_span_draft_boundary() -> None:
    # WHY: each draft is an independent reply; a `must` pattern must match within
    # a single draft, not across two drafts joined together.
    case = EvalCase(
        id="x", conversation=["c"], rubric_focus=[],
        must=["满99包邮"], must_not=[], notes="",
    )
    # "满99" in one draft, "包邮" in another — must NOT count as a match.
    result = check_must_constraints(case, ["满99", "包邮"])
    assert not result.passed


def test_summarize_aggregates_pass_rate() -> None:
    rows = [
        {"id": "a", "deterministic_passed": True, "judge": {"grounded": True}},
        {"id": "b", "deterministic_passed": False, "judge": {"grounded": False}},
    ]
    summary = summarize(rows)
    assert summary["total"] == 2
    assert summary["deterministic_pass_rate"] == 0.5
    assert summary["judge_pass_rate"]["grounded"] == 0.5
