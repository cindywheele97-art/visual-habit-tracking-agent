"""Offline, billable eval runner. Run: python -m evals  (needs ANTHROPIC_API_KEY).

Builds a real Agent + real judge client, runs all golden cases, prints a report.
Kept out of pytest: it makes real model calls."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from glimpse_brain.agent import Agent
from glimpse_brain.config import load_config
from glimpse_brain.evals_pkg.distill import (
    DISTILL_SYSTEM,
    candidate_id,
    record_to_prompt,
    response_to_case,
)
from glimpse_brain.evals_pkg.harness import load_cases, summarize
from glimpse_brain.evals_pkg.runner import run_case
from glimpse_brain.feedback import FeedbackLog
from glimpse_brain.knowledge import OkfKnowledgeBase
from glimpse_brain.llm import AnthropicLLM, RateLimiter
from glimpse_brain.redaction import Redactor
from glimpse_brain.server import AGENT_SYSTEM
from glimpse_brain.tooluse import AnthropicToolUseClient

CASES_DIR = Path(__file__).parent / "cases"


async def _run() -> None:
    p = Path("~/.glimpse/glimpse.toml").expanduser()
    cfg = load_config(p if p.exists() else None)
    agent = Agent(
        client=AnthropicToolUseClient(cfg.llm.model),
        system=AGENT_SYSTEM,
        knowledge=OkfKnowledgeBase(
            catalog_dir=Path(cfg.brain.knowledge_dir),
            legacy_playbook=Path(cfg.brain.playbook),
        ),
        redactor=Redactor(cfg.redaction.patterns),
        limiter=RateLimiter(cfg.llm.max_calls_per_minute),
        max_suggestions=cfg.llm.max_suggestions,
        max_iterations=cfg.llm.max_iterations,
    )
    judge_client = AnthropicLLM()
    rows = []
    for case in load_cases(CASES_DIR):
        row = await run_case(case, agent=agent, judge_client=judge_client, model=cfg.llm.model)
        rows.append(row)
        status = "PASS" if row["deterministic_passed"] else "FAIL"
        print(f"[{status}] {case.id}  judge={row['judge']}  tools={row['tools_used']}")
        for f in row["deterministic_failures"]:
            print(f"    - {f}")
    print("\n=== SUMMARY ===")
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=2))


async def _distill() -> None:
    p = Path("~/.glimpse/glimpse.toml").expanduser()
    cfg = load_config(p if p.exists() else None)
    redactor = Redactor(cfg.redaction.patterns)
    corpus = FeedbackLog(Path(cfg.brain.feedback_log), redactor)
    client = AnthropicLLM()
    candidates_dir = CASES_DIR / "candidates"
    candidates_dir.mkdir(parents=True, exist_ok=True)
    seen = {p.stem for p in CASES_DIR.glob("*.json")} | {
        p.stem for p in candidates_dir.glob("*.json")
    }
    for record in corpus.read():
        if record.verdict != "down" or not record.note:
            continue
        cid = candidate_id(record)
        if cid in seen:
            continue
        try:
            raw = await client.complete(
                system=DISTILL_SYSTEM,
                user=record_to_prompt(record),
                model=cfg.llm.model,
            )
        except Exception as exc:  # per-record fail-soft: skip, keep going
            print(f"[skip] {cid}: {exc}")
            continue
        case = response_to_case(raw, cid, record.conversation)
        (candidates_dir / f"{cid}.json").write_text(
            json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        seen.add(cid)
        print(f"[candidate] {cid}  must={case['must']}  must_not={case['must_not']}")


def promote(case_id: str) -> None:
    src = CASES_DIR / "candidates" / f"{case_id}.json"
    dst = CASES_DIR / f"{case_id}.json"
    if not src.exists():
        print(f"no candidate: {case_id}")
        return
    src.rename(dst)
    print(f"promoted {case_id} -> {dst}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        asyncio.run(_run())
    elif args[0] == "distill":
        asyncio.run(_distill())
    elif args[0] == "promote" and len(args) == 2:
        promote(args[1])
    else:
        print("usage: python -m evals [distill | promote <id>]")
        raise SystemExit(2)
