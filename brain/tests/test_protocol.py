"""Test the NDJSON wire protocol contract."""

from __future__ import annotations

import pytest

from glimpse_brain.protocol import (
    AckMsg,
    Block,
    CopiedMsg,
    HelloMsg,
    OcrMsg,
    ProtocolError,
    StatusMsg,
    SuggestionItem,
    SuggestionsMsg,
    parse_inbound,
    to_line,
)


def test_ocr_roundtrip() -> None:
    # The contract: shell sends structured blocks with normalized x-extents,
    # which the tracker needs for inbound/outbound classification.
    msg = OcrMsg(
        seq=7,
        ts="2026-06-11T12:00:00Z",
        region_id="region-1",
        blocks=[Block(text="你好", x0=0.05, x1=0.4, conf=0.97)],
    )
    parsed = parse_inbound(to_line(msg))
    assert isinstance(parsed, OcrMsg)
    assert parsed.seq == 7
    assert parsed.blocks[0].x1 == 0.4


def test_parse_dispatches_on_type() -> None:
    assert isinstance(parse_inbound('{"type":"hello","shell_version":"0.1.0"}'), HelloMsg)
    assert isinstance(
        parse_inbound('{"type":"copied","suggestion_id":"s1","region_id":"region-1"}'),
        CopiedMsg,
    )


def test_unknown_type_fails_loud() -> None:
    with pytest.raises(ProtocolError):
        parse_inbound('{"type":"mystery"}')


def test_extra_fields_rejected() -> None:
    # Typos must fail loudly, not silently (house rule).
    with pytest.raises(ProtocolError):
        parse_inbound('{"type":"hello","shell_version":"0.1.0","extra":1}')


def test_outbound_serialization_is_one_line() -> None:
    sug = SuggestionsMsg(
        region_id="region-1", items=[SuggestionItem(id="s1", text="好的，亲")]
    )
    line = to_line(sug)
    assert line.endswith("\n") and "\n" not in line[:-1]
    ack = to_line(AckMsg(seq=3))
    assert '"seq":3' in ack
    status = to_line(StatusMsg(state="watching"))
    assert '"state":"watching"' in status
