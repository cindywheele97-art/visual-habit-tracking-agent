"""Eval harness mechanics: load golden cases, run deterministic must/must_not
checks, aggregate scores. Pure + deterministic — the model is only used by the
judge (judge.py), never here."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvalCase:
    id: str
    conversation: list[str]
    rubric_focus: list[str]
    must: list[str] = field(default_factory=list)
    must_not: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass(frozen=True)
class ConstraintResult:
    passed: bool
    failures: list[str]


def load_cases(directory: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        cases.append(
            EvalCase(
                id=data["id"],
                conversation=data["conversation"],
                rubric_focus=data.get("rubric_focus", []),
                must=data.get("must", []),
                must_not=data.get("must_not", []),
                notes=data.get("notes", ""),
            )
        )
    return cases


def check_must_constraints(case: EvalCase, drafts: list[str]) -> ConstraintResult:
    """`must` = at least one draft matches each pattern; `must_not` = no draft
    matches any pattern. Patterns are matched per-draft (each draft is an
    independent reply), never against a joined blob, so a pattern can't span a
    draft boundary."""
    failures: list[str] = []
    for pattern in case.must:
        if not any(re.search(pattern, draft) for draft in drafts):
            failures.append(f"must-missing: {pattern}")
    for pattern in case.must_not:
        if any(re.search(pattern, draft) for draft in drafts):
            failures.append(f"must_not-present: {pattern}")
    return ConstraintResult(passed=not failures, failures=failures)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    det_pass = sum(1 for r in rows if r["deterministic_passed"])
    judge_dims: dict[str, list[bool]] = {}
    for r in rows:
        for dim, ok in r.get("judge", {}).items():
            judge_dims.setdefault(dim, []).append(bool(ok))
    return {
        "total": total,
        "deterministic_pass_rate": (det_pass / total) if total else 0.0,
        "judge_pass_rate": {
            dim: (sum(vals) / len(vals)) for dim, vals in judge_dims.items()
        },
    }
