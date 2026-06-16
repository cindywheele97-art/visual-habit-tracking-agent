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
