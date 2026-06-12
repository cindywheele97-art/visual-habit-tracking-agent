from __future__ import annotations

from glimpse_brain.redaction import Redactor


def test_redacts_cn_mobile_and_long_digit_runs() -> None:
    # Privacy rule: PII must not reach the LLM or the event log.
    r = Redactor([r"1[3-9]\d{9}", r"\d{10,}"])
    assert "13812345678" not in r.redact("我的电话是13812345678，订单20260611000123")
    assert "20260611000123" not in r.redact("订单20260611000123")


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
