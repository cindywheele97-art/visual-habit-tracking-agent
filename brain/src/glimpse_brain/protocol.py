"""NDJSON wire protocol between the Swift shell and the Python brain."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class ProtocolError(ValueError):
    """A line on the wire could not be parsed into a known message."""


class Block(BaseModel):
    """One OCR text block; x0/x1 (and y0/y1, top-left origin) are normalized
    [0,1] within the region. y defaults exist for pre-P7.2 shells."""

    model_config = ConfigDict(extra="forbid")
    text: str
    x0: float = Field(ge=0.0, le=1.0)
    x1: float = Field(ge=0.0, le=1.0)
    conf: float = Field(ge=0.0, le=1.0)
    y0: float = Field(default=0.0, ge=0.0, le=1.0)
    y1: float = Field(default=0.0, ge=0.0, le=1.0)


class OcrMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["ocr"] = "ocr"
    seq: int
    ts: str
    region_id: str
    blocks: list[Block]
    contact: str = ""  # OCR'd customer name from the contact-name region; "" = unknown
    image: str = ""  # optional base64 JPEG of the conversation region; "" = none


class HelloMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["hello"] = "hello"
    shell_version: str


class CopiedMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["copied"] = "copied"
    suggestion_id: str
    region_id: str


class ClickMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["click"] = "click"
    ts: str
    app: str
    x: float
    y: float
    blocks: list[Block]
    window_title: str = ""  # clicked window's title — flywheel join-key context
    url: str = ""  # focused browser document URL (best-effort AX read)
    # False = metadata-only click (burst coalescing or snapshot/OCR failure):
    # the click FACT always survives even when its screenshot doesn't.
    capture_ok: bool = True


class DwellMsg(BaseModel):
    """A closed attention interval on one window/page (audit §三 B2)."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["dwell"] = "dwell"
    app: str
    window_title: str = ""
    url: str = ""
    start_ts: str
    end_ts: str
    seconds: float = Field(ge=0.0)


class RepliedMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["replied"] = "replied"
    suggestion_id: str
    region_id: str
    mode: Literal["fill", "sent", "cancelled"]


class FeedbackMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["feedback"] = "feedback"
    suggestion_id: str
    region_id: str
    verdict: Literal["up", "down"]
    note: str = ""  # free-text correction; meaningful with "down"


class SummarizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["summarize"] = "summarize"


class AckMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["ack"] = "ack"
    seq: int


class SuggestionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    text: str


class SuggestionsMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["suggestions"] = "suggestions"
    region_id: str
    items: list[SuggestionItem]
    stale: bool = False  # always on the wire; Swift mirror decodes non-optional


class StatusMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["status"] = "status"
    state: Literal["watching", "thinking", "degraded", "error"]
    detail: str = ""  # always on the wire; Swift mirror decodes non-optional


class SummaryMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["summary"] = "summary"
    text: str


class AdvisoryMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["advisory"] = "advisory"
    text: str


InboundMsg = (
    OcrMsg
    | HelloMsg
    | CopiedMsg
    | ClickMsg
    | DwellMsg
    | SummarizeRequest
    | RepliedMsg
    | FeedbackMsg
)
OutboundMsg = AckMsg | SuggestionsMsg | StatusMsg | SummaryMsg | AdvisoryMsg

_INBOUND: TypeAdapter[InboundMsg] = TypeAdapter(
    Annotated[InboundMsg, Field(discriminator="type")]
)


def parse_inbound(line: str) -> InboundMsg:
    try:
        return _INBOUND.validate_json(line)
    except ValidationError as exc:
        raise ProtocolError(str(exc)) from exc
    except ValueError as exc:  # malformed JSON (not a schema error)
        raise ProtocolError(f"invalid JSON: {exc}") from exc


def to_line(msg: BaseModel) -> str:
    return msg.model_dump_json() + "\n"
