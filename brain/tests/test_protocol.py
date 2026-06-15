"""Test the NDJSON wire protocol contract."""

from __future__ import annotations

import pytest

from glimpse_brain.protocol import (
    AckMsg,
    Block,
    ClickMsg,
    CopiedMsg,
    HelloMsg,
    OcrMsg,
    ProtocolError,
    RepliedMsg,
    StatusMsg,
    SuggestionItem,
    SuggestionsMsg,
    SummarizeRequest,
    SummaryMsg,
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


def test_malformed_json_raises_protocol_error() -> None:
    # Reader loops catch ProtocolError broadly; a truncated write or empty
    # line must not leak a bare ValueError past that guard.
    with pytest.raises(ProtocolError):
        parse_inbound("not json at all")
    with pytest.raises(ProtocolError):
        parse_inbound("")


def test_defaults_always_serialized() -> None:
    # Swift Codable mirror uses non-optional fields for these; they must
    # appear on the wire even at their default values.
    line = to_line(SuggestionsMsg(region_id="r", items=[]))
    assert '"stale":false' in line
    line = to_line(StatusMsg(state="watching"))
    assert '"detail":""' in line


def test_click_roundtrip() -> None:
    # WHY: click events carry the OCR'd text near the click — the raw material
    # the summarizer interprets. Field names must match the Swift mirror.
    msg = ClickMsg(
        ts="2026-06-12T09:00:00Z",
        app="com.google.Chrome",
        x=120.5,
        y=240.0,
        blocks=[Block(text="Adidas Ultraboost", x0=0.1, x1=0.5, conf=0.9)],
    )
    parsed = parse_inbound(to_line(msg))
    assert isinstance(parsed, ClickMsg)
    assert parsed.app == "com.google.Chrome"
    assert parsed.blocks[0].text == "Adidas Ultraboost"


def test_summarize_request_parses() -> None:
    assert isinstance(parse_inbound('{"type":"summarize"}'), SummarizeRequest)


def test_summary_msg_serializes_single_line() -> None:
    line = to_line(SummaryMsg(text="今天你看了 3 双 Adidas"))
    assert line.endswith("\n") and "\n" not in line[:-1]
    assert '"type":"summary"' in line
    assert '"text":' in line


def test_replied_roundtrip() -> None:
    # WHY: the shell reports each fill/send so the brain keeps an audit trail of
    # actions taken on real people. mode is a closed set — typos must fail loud.
    msg = RepliedMsg(suggestion_id="s1", region_id="region-1", mode="sent")
    parsed = parse_inbound(to_line(msg))
    assert isinstance(parsed, RepliedMsg)
    assert parsed.suggestion_id == "s1"
    assert parsed.mode == "sent"


def test_replied_rejects_unknown_mode() -> None:
    with pytest.raises(ProtocolError):
        parse_inbound(
            '{"type":"replied","suggestion_id":"s1","region_id":"r","mode":"bogus"}'
        )


def test_ocr_contact_defaults_empty_and_roundtrips() -> None:
    # WHY: contact is the memory key; it must be optional (old shells omit it)
    # and survive the wire.
    line = '{"type":"ocr","seq":1,"ts":"t","region_id":"r","blocks":[]}'
    parsed = parse_inbound(line)
    assert isinstance(parsed, OcrMsg)
    assert parsed.contact == ""  # back-compatible default

    msg = OcrMsg(seq=2, ts="t", region_id="r", blocks=[], contact="小明")
    assert parse_inbound(to_line(msg)).contact == "小明"
