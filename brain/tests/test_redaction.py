from __future__ import annotations

from glimpse_brain.redaction import Redactor


def test_redacts_cn_mobile_and_long_digit_runs() -> None:
    # Privacy rule: PII must not reach the LLM or the event log.
    r = Redactor([r"1[3-9]\d{9}", r"\d{10,}"])
    assert "13812345678" not in r.redact("我的电话是13812345678，订单20260611000123")
    assert "20260611000123" not in r.redact("订单20260611000123")


def test_default_patterns_redact_digits_embedded_in_chinese_text() -> None:
    # WHY: CJK characters count as \w in Python's re, so a \b-anchored pattern
    # never fires between 号 and a digit — exactly the context Chinese chat
    # text puts card/order numbers in. The DEFAULT config patterns must catch
    # them, or the product's main audience leaks PII to the LLM.
    from glimpse_brain.config import RedactionCfg

    r = Redactor(RedactionCfg().patterns)
    masked = r.redact("卡号6212345678901234请查收")
    assert "6212345678901234" not in masked
    assert "卡号" in masked and "请查收" in masked
    # Short numbers (prices, quantities) stay readable.
    assert r.redact("一共99元，买了3件") == "一共99元，买了3件"


def test_default_patterns_redact_email_and_separated_numbers() -> None:
    # WHY: email and spaced/dashed card numbers leak PII if defaults only catch contiguous digits.
    from glimpse_brain.config import RedactionCfg

    r = Redactor(RedactionCfg().patterns)
    assert "a.b@test.com" not in r.redact("联系 a.b@test.com 谢谢")
    assert "6212 3456 7890 1234" not in r.redact("卡号6212 3456 7890 1234")
    assert "621-234-567-890-1" not in r.redact("卡621-234-567-890-1")
    # Over-redaction is as bad as under-redaction — prices and short hotlines stay readable.
    assert r.redact("一共99元，买了3件") == "一共99元，买了3件"
    assert r.redact("电话 400-123") == "电话 400-123"
    # Datetimes (dash date + space + time) must NOT be mistaken for a card/phone
    # run: the separated pattern keeps its separator homogeneous. 发货/下单时间
    # is core e-commerce CS context the draft needs.
    assert r.redact("发货时间 2026-07-05 12:30:45 已寄出") == "发货时间 2026-07-05 12:30:45 已寄出"
    assert r.redact("订单 2026-07-05 下单") == "订单 2026-07-05 下单"


def test_separated_pattern_still_catches_spaced_and_dashed_cards() -> None:
    # WHY: the homogeneous-separator refinement must not regress the real target
    # — spaced/dashed cards and spaced mobiles still get redacted.
    from glimpse_brain.config import RedactionCfg

    r = Redactor(RedactionCfg().patterns)
    assert "138 1234 5678" not in r.redact("手机 138 1234 5678")  # spaced mobile
    assert "6212 3456 7890 1234" not in r.redact("卡号6212 3456 7890 1234")
    assert "6212-3456-7890-1234" not in r.redact("卡6212-3456-7890-1234")


def test_clean_text_untouched() -> None:
    r = Redactor([r"1[3-9]\d{9}"])
    assert r.redact("你好，多少钱？") == "你好，多少钱？"


def test_redact_payload_walks_nested_values() -> None:
    r = Redactor([r"1[3-9]\d{9}"])
    payload: dict[str, object] = {
        "items": ["电话13812345678", "ok"],
        "nested": {"a": "13912345678"},
        "n": 42,
    }
    out = r.redact_payload(payload)
    assert "13812345678" not in str(out)
    assert "13912345678" not in str(out)
    assert out["n"] == 42


def test_redact_payload_walks_tuples() -> None:
    # Privacy boundary must hold for every container type that can carry text.
    r = Redactor([r"1[3-9]\d{9}"])
    out = r.redact_payload({"t": ("13812345678", "ok")})
    assert "13812345678" not in str(out)
