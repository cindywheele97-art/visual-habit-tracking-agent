"""Regex redaction applied before any text leaves the machine or hits disk."""

from __future__ import annotations

import re

_MASK = "▮▮▮"


class Redactor:
    def __init__(self, patterns: list[str]) -> None:
        self._compiled = [re.compile(p) for p in patterns]

    def redact(self, text: str) -> str:
        for pattern in self._compiled:
            text = pattern.sub(_MASK, text)
        return text

    def redact_payload(self, payload: dict[str, object]) -> dict[str, object]:
        return {key: self._redact_value(value) for key, value in payload.items()}

    def _redact_value(self, value: object) -> object:
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, list):
            return [self._redact_value(v) for v in value]
        if isinstance(value, dict):
            return {k: self._redact_value(v) for k, v in value.items()}
        return value
