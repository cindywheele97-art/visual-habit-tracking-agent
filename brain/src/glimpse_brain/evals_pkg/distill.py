"""Pure distillation: turn one human correction into a candidate eval-case dict.
The model call lives in the offline CLI (evals/__main__.py); this module only
builds the prompt and parses the response, so it is testable without the API.
The `conversation` field comes from the record (the real exchange), never the
model — only rubric_focus/must/must_not/notes are model-authored."""

from __future__ import annotations

import hashlib
import json

from glimpse_brain.feedback import FeedbackRecord

DISTILL_SYSTEM = """\
你是一名客服质量评审，把一条人工修正提炼成一个回归测试用例。
给定：对话、被否决的 AI 草稿、人工修正建议。
只输出一个 JSON 对象，键如下，不要输出任何其他内容：
- rubric_focus: 字符串数组，取自 grounded/tone/handles_uncertainty/safe
- must: 正则字符串数组，合格回复至少应命中其一（可为空）
- must_not: 正则字符串数组，合格回复不得命中任何一个（可为空）
- notes: 一句话说明这条用例考察什么"""


def candidate_id(record: FeedbackRecord) -> str:
    key = "\x00".join([*record.conversation, record.draft, record.note])
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    return f"fb-{digest}"


def record_to_prompt(record: FeedbackRecord) -> str:
    return (
        "对话：\n"
        + "\n".join(record.conversation)
        + "\n\n被否决的 AI 草稿：\n"
        + record.draft
        + "\n\n人工修正建议：\n"
        + record.note
    )


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def response_to_case(raw: str, case_id: str, conversation: list[str]) -> dict:
    decoder = json.JSONDecoder()
    body: dict = {}
    for i, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(raw[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            body = candidate
            break
    notes = body.get("notes", "")
    return {
        "id": case_id,
        "conversation": conversation,
        "rubric_focus": _strings(body.get("rubric_focus")),
        "must": _strings(body.get("must")),
        "must_not": _strings(body.get("must_not")),
        "notes": notes if isinstance(notes, str) else "",
    }
