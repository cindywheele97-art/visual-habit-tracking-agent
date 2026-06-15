"""Vision tool: lets the agent look at the current conversation-region image.
Returns a ToolImage (an Anthropic image block) the agent sees in its next turn."""

from __future__ import annotations

from typing import Any

from glimpse_brain.tooluse import ToolImage


class LookTool:
    name = "look_at_conversation"
    description = "查看当前对话区域的截图。客户可能发来商品图片/截图时调用，看清后再回复。"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    def __init__(self, image_b64: str, media_type: str = "image/jpeg") -> None:
        self._image_b64 = image_b64
        self._media_type = media_type

    async def run(self, input: dict[str, Any]) -> str | ToolImage:
        if not self._image_b64:
            return "（暂无可查看的图片）"
        return ToolImage(image_b64=self._image_b64, media_type=self._media_type)
