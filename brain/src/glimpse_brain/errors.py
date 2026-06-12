"""Brain-specific exceptions. All inherit from GlimpseError for blanket handling."""

from __future__ import annotations


class GlimpseError(Exception):
    """Base for all brain errors."""


class CostCapExceeded(GlimpseError):
    """LLM call rate cap hit; suggestions pause until the window clears."""


class SuggestionParseError(GlimpseError):
    """LLM returned something we could not parse into suggestions."""
