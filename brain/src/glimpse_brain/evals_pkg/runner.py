"""Per-case eval: run the agent, apply deterministic checks + the judge."""

from __future__ import annotations

from typing import Any

from glimpse_brain.evals_pkg.harness import EvalCase, check_must_constraints
from glimpse_brain.evals_pkg.judge import judge_drafts
from glimpse_brain.llm import LLMClient


async def run_case(
    case: EvalCase, *, agent: Any, judge_client: LLMClient, model: str
) -> dict[str, Any]:
    result = await agent.suggest(case.conversation)
    constraints = check_must_constraints(case, result.drafts)
    judge = (
        await judge_drafts(
            judge_client,
            model=model,
            conversation=case.conversation,
            drafts=result.drafts,
            rubric_focus=case.rubric_focus,
        )
        if case.rubric_focus
        else {}
    )
    return {
        "id": case.id,
        "drafts": result.drafts,
        "tools_used": result.tools_used,
        "deterministic_passed": constraints.passed,
        "deterministic_failures": constraints.failures,
        "judge": judge,
    }
