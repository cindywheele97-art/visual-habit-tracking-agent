from __future__ import annotations

import json
from pathlib import Path

from glimpse_brain.evals_pkg.harness import (
    EvalCase,
    check_must_constraints,
    load_cases,
    summarize,
)
from glimpse_brain.evals_pkg.judge import judge_drafts, parse_judge_json


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


def test_parse_judge_json_extracts_dimension_verdicts() -> None:
    raw = '前言 {"grounded": true, "tone": false, "safe": true} 结尾'
    out = parse_judge_json(raw, ["grounded", "tone", "safe"])
    assert out == {"grounded": True, "tone": False, "safe": True}


def test_parse_judge_json_missing_dim_defaults_false() -> None:
    # WHY: a judge that omits a dimension must fail-closed (count as not-passed),
    # never silently pass.
    out = parse_judge_json('{"grounded": true}', ["grounded", "tone"])
    assert out == {"grounded": True, "tone": False}


def test_parse_judge_json_survives_prose_wrapping() -> None:
    # WHY: models often wrap JSON in prose; a valid verdict must not be lost
    # (which would silently under-report quality). Both leading and trailing
    # prose — including stray braces — must be tolerated.
    out = parse_judge_json('评审结果如下 {"grounded": true} 仅供参考', ["grounded"])
    assert out == {"grounded": True}
    out2 = parse_judge_json('维度 {grounded}. {"grounded": true}', ["grounded"])
    assert out2 == {"grounded": True}


def test_parse_judge_json_non_bool_fails_closed() -> None:
    # WHY: strict fail-closed — a judge returning 1 / "true" instead of a real
    # boolean must NOT be promoted to a pass.
    out = parse_judge_json('{"grounded": 1, "tone": "true"}', ["grounded", "tone"])
    assert out == {"grounded": False, "tone": False}


async def test_judge_drafts_uses_client_and_focus() -> None:
    class FakeJudge:
        async def complete(self, *, system: str, user: str, model: str) -> str:
            assert "grounded" in system  # rubric dims passed into the prompt
            return '{"grounded": true}'

    verdicts = await judge_drafts(
        FakeJudge(), model="m", conversation=["客户: x"], drafts=["回复"],
        rubric_focus=["grounded"],
    )
    assert verdicts == {"grounded": True}


async def test_run_case_combines_deterministic_and_judge() -> None:
    from glimpse_brain.evals_pkg.harness import EvalCase
    from glimpse_brain.evals_pkg.runner import run_case

    case = EvalCase(
        id="x", conversation=["客户: 包邮吗"], rubric_focus=["grounded"],
        must=["包邮"], must_not=[], notes="",
    )

    class FakeAgent:
        async def suggest(self, tail):
            from glimpse_brain.agent import AgentResult
            return AgentResult(drafts=["亲，满99包邮"], tools_used=["knowledge_base"])

    class FakeJudgeClient:
        async def complete(self, *, system, user, model) -> str:
            return '{"grounded": true}'

    row = await run_case(case, agent=FakeAgent(), judge_client=FakeJudgeClient(), model="m")
    assert row["id"] == "x"
    assert row["deterministic_passed"] is True
    assert row["judge"] == {"grounded": True}
    assert row["tools_used"] == ["knowledge_base"]
