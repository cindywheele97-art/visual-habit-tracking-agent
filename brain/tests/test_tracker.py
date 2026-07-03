from __future__ import annotations

from glimpse_brain.protocol import Block
from glimpse_brain.tracker import ConversationTracker


def _b(text: str, x0: float = 0.05, x1: float = 0.4, conf: float = 0.95) -> Block:
    return Block(text=text, x0=x0, x1=x1, conf=conf)


def _own(text: str) -> Block:  # right-aligned bubble = our own reply
    return Block(text=text, x0=0.6, x1=0.95, conf=0.95)


def make_tracker() -> ConversationTracker:
    return ConversationTracker(
        min_confidence=0.5, side_threshold=0.5, ignore_patterns=[r"^\d{1,2}:\d{2}$"]
    )


def test_new_inbound_message_detected_once() -> None:
    # WHY: duplicate suggestions for one message destroy user trust (spec §8.2).
    t = make_tracker()
    first = t.ingest([_b("你好，在吗？")])
    assert first.new_inbound == ["你好，在吗？"]
    again = t.ingest([_b("你好，在吗？")])  # re-OCR of static screen
    assert again.new_inbound == []


def test_own_reply_does_not_trigger() -> None:
    # WHY: replying to our own replies would loop the agent on itself.
    t = make_tracker()
    t.ingest([_b("多少钱？")])
    result = t.ingest([_b("多少钱？"), _own("99元包邮哦")])
    assert result.new_inbound == []
    assert result.new_outbound == ["99元包邮哦"]


def test_append_only_new_tail_is_reported() -> None:
    t = make_tracker()
    t.ingest([_b("第一条")])
    result = t.ingest([_b("第一条"), _b("第二条"), _b("第三条")])
    assert result.new_inbound == ["第二条", "第三条"]


def test_repeated_text_new_message_still_detected() -> None:
    # WHY: customers repeat themselves constantly ("好的", "在吗"). Global text
    # dedup must not eat a genuinely NEW message just because the same words
    # were said before — that permanently silences suggestions for exactly the
    # most common customer messages.
    t = make_tracker()
    t.ingest([_b("在吗")])
    t.ingest([_b("在吗"), _own("在的，亲")])
    result = t.ingest([_b("在吗"), _own("在的，亲"), _b("在吗")])
    assert result.new_inbound == ["在吗"]
    # Re-OCR of the now-static screen must not re-fire.
    again = t.ingest([_b("在吗"), _own("在的，亲"), _b("在吗")])
    assert again.new_inbound == []


def test_append_with_top_lines_scrolled_off() -> None:
    # WHY: as the chat grows, old lines scroll out of the watched region; the
    # frame is still an append and the new bottom line must be detected.
    t = make_tracker()
    t.ingest([_b("一"), _b("二"), _b("三")])
    result = t.ingest([_b("二"), _b("三"), _b("四")])
    assert result.new_inbound == ["四"]


def test_scroll_up_then_back_down_does_not_refire() -> None:
    # WHY: scrolling back to the bottom re-shows lines that positionally look
    # like an append against the scrolled-up frame. A scrolled view must not
    # anchor the append-diff — otherwise handled messages re-fire as new and
    # produce auto-send-eligible drafts for them.
    t = make_tracker()
    t.ingest([_b("旧A"), _b("旧B"), _b("旧C")])
    assert t.ingest([_b("更旧"), _b("历史"), _b("旧A")]).new_inbound == []  # scrolled up
    back = t.ingest([_b("旧A"), _b("旧B"), _b("旧C")])  # scrolled back down
    assert back.new_inbound == []
    assert t.tail() == ["客户: 旧A", "客户: 旧B", "客户: 旧C"]  # no duplicates


def test_new_message_after_scroll_back_re_anchors() -> None:
    # WHY: new messages only ever appear at the live bottom — seeing one
    # proves we are back at the bottom, and append-diff must resume so later
    # repeated-text messages are still caught.
    t = make_tracker()
    t.ingest([_b("旧A"), _b("旧B")])
    t.ingest([_b("历史"), _b("旧A")])  # scrolled up
    t.ingest([_b("旧A"), _b("旧B")])  # back down, nothing new
    fresh = t.ingest([_b("旧B"), _b("新消息")])
    assert fresh.new_inbound == ["新消息"]
    repeat = t.ingest([_b("旧B"), _b("新消息"), _b("旧A")])  # 旧A repeated as NEW text
    assert repeat.new_inbound == ["旧A"]


def test_scroll_back_does_not_trigger() -> None:
    # WHY: scrolling up surfaces OLD lines at the top; the bottom line is one
    # we've already seen, so the new-tail scan must come up empty.
    t = make_tracker()
    t.ingest([_b("旧消息A"), _b("旧消息B")])
    result = t.ingest([_b("更旧的消息"), _b("旧消息A"), _b("旧消息B")])
    assert result.new_inbound == []


def test_timestamp_lines_ignored() -> None:
    t = make_tracker()
    result = t.ingest([_b("12:30", x0=0.45, x1=0.55), _b("你好")])
    assert result.new_inbound == ["你好"]


def test_low_confidence_snapshot_rejected() -> None:
    # WHY: OCR garbage is the #1 source of phantom "new messages" (spec §5).
    t = make_tracker()
    result = t.ingest([_b("乱码乱码", conf=0.2)])
    assert not result.accepted
    assert result.reason == "low-confidence"


def test_tail_formats_speakers() -> None:
    t = make_tracker()
    t.ingest([_b("在吗"), _own("在的")])
    assert t.tail() == ["客户: 在吗", "我: 在的"]


def test_confidence_gate_ignores_filtered_blocks() -> None:
    # WHY: a low-confidence timestamp must not veto high-confidence messages.
    t = ConversationTracker(
        min_confidence=0.8, side_threshold=0.5, ignore_patterns=[r"^\d{1,2}:\d{2}$"]
    )
    result = t.ingest(
        [
            Block(text="12:30", x0=0.45, x1=0.55, conf=0.1),
            Block(text="你好", x0=0.05, x1=0.4, conf=0.95),
        ]
    )
    assert result.accepted
    assert result.new_inbound == ["你好"]


def test_seen_eviction_is_bounded_and_behavioral() -> None:
    # WHY: bounded memory wins over perfect dedup — after the cap rolls,
    # an evicted ancient line may trigger once more; unbounded growth never.
    t = ConversationTracker(
        min_confidence=0.5, side_threshold=0.5, ignore_patterns=[], max_seen=3
    )
    for i in range(5):
        t.ingest([Block(text=f"m{i}", x0=0.05, x1=0.4, conf=0.9)])
    # m0 has been evicted from the seen-set, so it re-triggers exactly as a
    # genuinely-new line would.
    result = t.ingest([Block(text="m0", x0=0.05, x1=0.4, conf=0.9)])
    assert result.new_inbound == ["m0"]
