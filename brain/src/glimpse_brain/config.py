"""TOML configuration for the brain. One file, validated loudly."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BrainCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    socket_path: str = Field(default_factory=lambda: str(Path("~/.glimpse/glimpse.sock").expanduser()))
    event_log: str = Field(default_factory=lambda: str(Path("~/.glimpse/events.jsonl").expanduser()))
    playbook: str = Field(default_factory=lambda: str(Path("~/.glimpse/playbook.md").expanduser()))

    @field_validator("socket_path", "event_log", "playbook", mode="before")
    @classmethod
    def _expand(cls, v: str) -> str:
        return str(Path(v).expanduser())


class LlmCfg(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    model: str = "claude-sonnet-4-6"
    max_calls_per_minute: int = Field(default=6, ge=1)
    max_suggestions: int = Field(default=3, ge=1, le=5)


class TrackerCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settle_ms: int = Field(default=800, ge=0)
    min_ocr_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    side_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    ignore_patterns: list[str] = Field(default_factory=lambda: [r"^\d{1,2}:\d{2}$"])


class RedactionCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patterns: list[str] = Field(
        default_factory=lambda: [r"1[3-9]\d{9}", r"\b\d{10,}\b"]
    )


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brain: BrainCfg = Field(default_factory=BrainCfg)
    llm: LlmCfg = Field(default_factory=LlmCfg)
    tracker: TrackerCfg = Field(default_factory=TrackerCfg)
    redaction: RedactionCfg = Field(default_factory=RedactionCfg)


def load_config(path: Path | None) -> Config:
    if path is None or not path.exists():
        return Config()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return Config.model_validate(data)
