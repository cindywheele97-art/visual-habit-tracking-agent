from __future__ import annotations

from glimpse_brain.tooluse import ToolImage
from glimpse_brain.vision_tool import LookTool


async def test_look_returns_held_image() -> None:
    tool = LookTool(image_b64="QUJD", media_type="image/jpeg")
    out = await tool.run({})
    assert isinstance(out, ToolImage)
    assert out.image_b64 == "QUJD" and out.media_type == "image/jpeg"
    assert tool.name == "look_at_conversation"
    assert tool.input_schema["required"] == []


async def test_look_with_no_image_is_friendly_text() -> None:
    # WHY: if the held image was cleared, looking must degrade to text, not crash.
    tool = LookTool(image_b64="")
    out = await tool.run({})
    assert isinstance(out, str) and "暂无" in out
