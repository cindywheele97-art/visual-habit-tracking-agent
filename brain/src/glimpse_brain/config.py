"""TOML configuration for the brain. One file, validated loudly."""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

log = logging.getLogger("glimpse.config")

# The pre-fix default digit pattern: \b never matches between CJK characters
# and digits, so it silently redacts nothing in Chinese text. Configs seeded
# before the fix still carry it — keeping it must be loud.
_LEGACY_DIGIT_PATTERN = r"\b\d{10,}\b"


class BrainCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    socket_path: str = Field(default_factory=lambda: str(Path("~/.glimpse/glimpse.sock").expanduser()))
    event_log: str = Field(default_factory=lambda: str(Path("~/.glimpse/events.jsonl").expanduser()))
    playbook: str = Field(default_factory=lambda: str(Path("~/.glimpse/playbook.md").expanduser()))
    feedback_log: str = Field(default_factory=lambda: str(Path("~/.glimpse/feedback.jsonl").expanduser()))
    knowledge_dir: str = Field(default_factory=lambda: str(Path("~/.glimpse/knowledge").expanduser()))

    @field_validator("socket_path", "event_log", "playbook", "feedback_log", "knowledge_dir", mode="before")
    @classmethod
    def _expand(cls, v: str) -> str:
        return str(Path(v).expanduser())


class LlmCfg(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    model: str = "claude-sonnet-4-6"
    # Screenshots are pixels — the regex redaction layer can't touch them.
    # Uploading them to the LLM (look_at_conversation) is opt-in only.
    send_images: bool = False
    max_calls_per_minute: int = Field(default=6, ge=1)
    max_suggestions: int = Field(default=3, ge=1, le=5)
    max_iterations: int = Field(default=4, ge=1, le=10)


class TrackerCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settle_ms: int = Field(default=800, ge=0)
    # 0.3, not 0.5: Vision's accurate-mode confidence for CJK screen text
    # commonly sits in 0.3-0.6; a 0.5 floor silently drops real Chinese
    # messages at the tracker gate. English text scores ~0.8+ either way.
    min_ocr_confidence: float = Field(default=0.3, ge=0.0, le=1.0)
    side_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    ignore_patterns: list[str] = Field(default_factory=lambda: [r"^\d{1,2}:\d{2}$"])


class RedactionCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Lookarounds, not \b: CJK characters are \w in Python's re, so \b never
    # fires between 号 and a digit — the exact context Chinese chat text puts
    # card/order numbers in.
    patterns: list[str] = Field(
        default_factory=lambda: [r"1[3-9]\d{9}", r"(?<!\d)\d{10,}(?!\d)"]
    )


class MemoryCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    palace_path: str = Field(default_factory=lambda: str(Path("~/.glimpse/palace").expanduser()))
    embedding_model: str = "embeddinggemma"
    recall_k: int = Field(default=5, ge=1, le=20)

    @field_validator("palace_path", mode="before")
    @classmethod
    def _expand(cls, v: str) -> str:
        return str(Path(v).expanduser())


class FeedbackCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    satisfaction_window: int = Field(default=20, ge=1)
    advisory_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    advisory_min_ratings: int = Field(default=20, ge=1)


class SkuCfg(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    enabled: bool = True
    model_path: str = Field(
        default_factory=lambda: str(Path("~/.glimpse/sku/cnclip_vitb16.img.onnx").expanduser())
    )
    index_path: str = Field(
        default_factory=lambda: str(Path("~/.glimpse/sku/index.npz").expanduser())
    )
    top_k: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("model_path", "index_path", mode="before")
    @classmethod
    def _expand(cls, v: str) -> str:
        return str(Path(v).expanduser())


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brain: BrainCfg = Field(default_factory=BrainCfg)
    llm: LlmCfg = Field(default_factory=LlmCfg)
    tracker: TrackerCfg = Field(default_factory=TrackerCfg)
    redaction: RedactionCfg = Field(default_factory=RedactionCfg)
    memory: MemoryCfg = Field(default_factory=MemoryCfg)
    feedback: FeedbackCfg = Field(default_factory=FeedbackCfg)
    sku: SkuCfg = Field(default_factory=SkuCfg)


def load_config(path: Path | None) -> Config:
    if path is None or not path.exists():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"malformed TOML in {path}: {exc}") from exc
    cfg = Config.model_validate(data)
    if _LEGACY_DIGIT_PATTERN in cfg.redaction.patterns:
        log.warning(
            "%s uses the legacy redaction pattern %r, which matches NOTHING in "
            "Chinese text (card/order numbers leak to the LLM). Replace it with "
            "%r.",
            path,
            _LEGACY_DIGIT_PATTERN,
            r"(?<!\d)\d{10,}(?!\d)",
        )
    return cfg
