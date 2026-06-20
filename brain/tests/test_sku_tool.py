from __future__ import annotations

import base64

from glimpse_brain.sku.tool import MatchSkuTool

IMG = base64.b64encode(b"jpeg-bytes").decode()


class FakeMatcher:
    def __init__(self, result, boom: bool = False) -> None:
        self._result = result
        self._boom = boom

    def match(self, jpeg_bytes: bytes):
        if self._boom:
            raise RuntimeError("embed failed")
        return self._result


async def test_formats_top_k_with_scores() -> None:
    tool = MatchSkuTool(FakeMatcher([("example-a", 0.82), ("example-b", 0.74)]), IMG)
    assert tool.name == "match_sku"
    out = await tool.run({})
    assert "example-a" in out and "0.82" in out
    assert "example-b" in out
    assert "read_knowledge" in out  # nudges the agent to read the candidate doc


async def test_no_image_is_friendly() -> None:
    tool = MatchSkuTool(FakeMatcher([("example-a", 0.9)]), "")
    assert "未找到相近商品" in await tool.run({})


async def test_no_matches_is_friendly() -> None:
    tool = MatchSkuTool(FakeMatcher([]), IMG)
    assert "未找到相近商品" in await tool.run({})


async def test_runtime_error_is_caught() -> None:
    tool = MatchSkuTool(FakeMatcher(None, boom=True), IMG)
    out = await tool.run({})
    assert "暂不可用" in out  # never raises into the agent loop
