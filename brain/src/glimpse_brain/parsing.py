"""Shared LLM-output parsing utilities."""

from __future__ import annotations

import json

from glimpse_brain.errors import SuggestionParseError


def parse_suggestions(raw: str, limit: int) -> list[str]:
    """Extract and validate suggestions from LLM output.

    Looks for a JSON array in the output, validates it contains strings,
    and returns up to `limit` items.

    Raises:
        SuggestionParseError: If JSON cannot be found or parsed.
    """
    # Known limitation: if the model emits TWO arrays separated by prose, the
    # first-'['..last-']' slice spans the prose and json.loads fails — which
    # surfaces as SuggestionParseError and degrades gracefully upstream.
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end <= start:
        raise SuggestionParseError(raw[:200])
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise SuggestionParseError(str(exc)) from exc
    items = [s.strip() for s in data if isinstance(s, str) and s.strip()]
    if not items:
        raise SuggestionParseError("no usable strings in LLM output")
    return items[:limit]
