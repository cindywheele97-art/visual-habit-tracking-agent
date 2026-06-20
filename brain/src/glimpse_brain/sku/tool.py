"""match_sku: embed the held conversation photo and return the nearest SKUs for
the agent to confirm via read_knowledge. Fail-soft to friendly text — never raises."""

from __future__ import annotations

import base64
from typing import Any

from glimpse_brain.sku.matcher import SkuMatcher


class MatchSkuTool:
    name = "match_sku"
    description = (
        "根据客户发来的商品图片，返回最相似的候选 SKU（商品 id 与相似度）；"
        "再用 read_knowledge 查看候选商品文档确认。"
    )
    input_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    def __init__(self, matcher: SkuMatcher, image_b64: str) -> None:
        self._matcher = matcher
        self._image_b64 = image_b64

    async def run(self, input: dict[str, Any]) -> str:
        if not self._image_b64:
            return "（未找到相近商品）"
        try:
            matches = self._matcher.match(base64.b64decode(self._image_b64))
        except Exception:  # decode/embed/query failure must not kill the pass
            return "（商品识别暂不可用）"
        if not matches:
            return "（未找到相近商品）"
        lines = [f"- {sku}（相似度 {score:.2f}）" for sku, score in matches]
        return "候选商品：\n" + "\n".join(lines) + "\n用 read_knowledge 查看候选商品文档确认。"
