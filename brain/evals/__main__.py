"""Offline, billable eval runner. Run: python -m evals  (needs ANTHROPIC_API_KEY).

Builds a real Agent + real judge client, runs all golden cases, prints a report.
Kept out of pytest: it makes real model calls."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from glimpse_brain.agent import Agent
from glimpse_brain.config import load_config
from glimpse_brain.evals_pkg.harness import load_cases, summarize
from glimpse_brain.evals_pkg.runner import run_case
from glimpse_brain.knowledge import FileKnowledgeBase
from glimpse_brain.llm import AnthropicLLM, RateLimiter
from glimpse_brain.redaction import Redactor
from glimpse_brain.server import AGENT_SYSTEM
from glimpse_brain.tooluse import AnthropicToolUseClient

CASES_DIR = Path(__file__).parent / "cases"


async def _main() -> None:
    cfg = load_config(Path("~/.glimpse/glimpse.toml").expanduser())
    agent = Agent(
        client=AnthropicToolUseClient(cfg.llm.model),
        system=AGENT_SYSTEM,
        knowledge=FileKnowledgeBase(playbook_path=Path(cfg.brain.playbook)),
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


if __name__ == "__main__":
    asyncio.run(_main())
