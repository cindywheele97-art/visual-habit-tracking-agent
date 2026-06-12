"""NDJSON wire protocol between the Swift shell and the Python brain."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class ProtocolError(ValueError):
    """A line on the wire could not be parsed into a known message."""


class Block(BaseModel):
    """One OCR text block; x0/x1 are normalized [0,1] within the region."""

    model_config = ConfigDict(extra="forbid")
    text: str
    x0: float = Field(ge=0.0, le=1.0)
    x1: float = Field(ge=0.0, le=1.0)
    conf: float = Field(ge=0.0, le=1.0)


class OcrMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["ocr"] = "ocr"
    seq: int
    ts: str
    region_id: str
    blocks: list[Block]


class HelloMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["hello"] = "hello"
    shell_version: str


class CopiedMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["copied"] = "copied"
    suggestion_id: str
    region_id: str


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
    stale: bool = False


class StatusMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["status"] = "status"
    state: Literal["watching", "thinking", "degraded", "error"]
    detail: str = ""


InboundMsg = OcrMsg | HelloMsg | CopiedMsg
OutboundMsg = AckMsg | SuggestionsMsg | StatusMsg

_INBOUND: TypeAdapter[InboundMsg] = TypeAdapter(
    Annotated[InboundMsg, Field(discriminator="type")]
)


def parse_inbound(line: str) -> InboundMsg:
    try:
        return _INBOUND.validate_json(line)
    except ValidationError as exc:
        raise ProtocolError(str(exc)) from exc


def to_line(msg: BaseModel) -> str:
    return msg.model_dump_json() + "\n"
