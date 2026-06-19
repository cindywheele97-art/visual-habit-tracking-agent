# SKU Visual Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the agent identify which catalog SKU a customer photo shows — an offline-built CLIP-ONNX visual index + a `match_sku` tool returning top-k candidates the agent resolves via the OKF catalog.

**Architecture:** A `sku/` subpackage: `SkuEmbedder` (CLIP image encoder via the existing onnxruntime, no torch), `SkuIndex` (numpy cosine nearest-neighbor, `.npz` on disk), `SkuMatcher` (facade carrying top_k/min_score), `MatchSkuTool` (`match_sku`), and an offline `build` CLI. The agent registers `match_sku` per-turn when an image is held and a matcher loaded; a matched SKU id is an OKF product doc id, so the agent follows with `read_knowledge`. Fail-soft to P6 (agent describes the photo) at every rung.

**Tech Stack:** Python 3.11 / numpy / onnxruntime / Pillow (new) / pytest. Chinese-CLIP ViT-B/16 image encoder exported to ONNX (obtained out-of-band). The Swift shell is untouched.

**Spec:** `docs/superpowers/specs/2026-06-19-sku-visual-matching-design.md`

**Conventions (every task):** `from __future__ import annotations`; PEP-604 `X | Y` unions (NOT `typing.Union`); full type annotations; frozen dataclasses for DTOs; interpreter `brain/.venv/bin/python`, linter `brain/.venv/bin/ruff`. Run all `git` from the repo root `/Users/john/Projects/visual-habit-tracking-agent`, as SEPARATE commands — do NOT chain `git commit` with `&&`, and never use `--amend`/`--no-verify` (a hook blocks them). The real CN-CLIP ONNX model is NOT available to subagents; everything except the opt-in `-m integration` test uses fakes.

---

## File Structure

**Create:**
- `brain/src/glimpse_brain/sku/__init__.py` (empty)
- `brain/src/glimpse_brain/sku/index.py` — `SkuIndex` (numpy cosine NN + npz).
- `brain/src/glimpse_brain/sku/embedder.py` — `SkuEmbedder` (Pillow preprocess + ONNX embed).
- `brain/src/glimpse_brain/sku/matcher.py` — `SkuMatcher` facade.
- `brain/src/glimpse_brain/sku/tool.py` — `MatchSkuTool`.
- `brain/src/glimpse_brain/sku/build.py` — offline index builder (`python -m glimpse_brain.sku.build`).
- `brain/tests/test_sku_index.py`, `test_sku_embedder.py`, `test_sku_matcher.py`, `test_sku_tool.py`, `test_sku_build.py`

**Modify:**
- `brain/pyproject.toml` — add `pillow`.
- `brain/src/glimpse_brain/config.py` — `SkuCfg` + `Config.sku`.
- `brain/src/glimpse_brain/agent.py` — `sku` param + per-turn `MatchSkuTool`.
- `brain/src/glimpse_brain/server.py` — `_build_sku` + wiring + `AGENT_SYSTEM` line.
- `brain/tests/test_agent.py`, `brain/tests/test_server.py` — new tests + `make_config`.
- `README.md` — model export + build + naming convention.

---

## Task 1: `SkuIndex` (numpy cosine nearest-neighbor)

**Files:**
- Create: `brain/src/glimpse_brain/sku/__init__.py`, `brain/src/glimpse_brain/sku/index.py`
- Test: `brain/tests/test_sku_index.py`

- [ ] **Step 1: Create the empty package init**

Create `brain/src/glimpse_brain/sku/__init__.py` with a single line:
```python
"""SKU visual matching: CLIP-ONNX embedding index over the product catalog."""
```

- [ ] **Step 2: Write the failing tests**

Create `brain/tests/test_sku_index.py`:
```python
from __future__ import annotations

from pathlib import Path

import numpy as np

from glimpse_brain.sku.index import SkuIndex


def unit(*vals: float) -> np.ndarray:
    v = np.array(vals, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_query_returns_nearest_by_cosine_descending() -> None:
    idx = SkuIndex(
        np.stack([unit(1, 0), unit(0, 1), unit(1, 1)]),
        ["east", "north", "ne"],
    )
    hits = idx.query(unit(1, 0.2), k=3)
    assert [sku for sku, _ in hits] == ["east", "ne", "north"]
    assert hits[0][1] > hits[1][1] > hits[2][1]  # scores descending


def test_query_respects_k() -> None:
    idx = SkuIndex(np.stack([unit(1, 0), unit(0, 1), unit(1, 1)]), ["a", "b", "c"])
    assert len(idx.query(unit(1, 0), k=2)) == 2


def test_empty_index_returns_empty() -> None:
    idx = SkuIndex(np.zeros((0, 0), dtype=np.float32), [])
    assert idx.query(unit(1, 0), k=5) == []
    assert len(idx) == 0


def test_save_load_round_trips(tmp_path: Path) -> None:
    idx = SkuIndex(np.stack([unit(1, 0), unit(0, 1)]), ["a", "b"])
    path = tmp_path / "index.npz"
    idx.save(path)
    loaded = SkuIndex.load(path)
    assert len(loaded) == 2
    hits = loaded.query(unit(1, 0), k=1)
    assert hits[0][0] == "a"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd brain && .venv/bin/python -m pytest tests/test_sku_index.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'glimpse_brain.sku.index'`.

- [ ] **Step 4: Implement `SkuIndex`**

Create `brain/src/glimpse_brain/sku/index.py`:
```python
"""Cosine nearest-neighbor over L2-normalized SKU vectors. Brute-force numpy —
ample at a few-thousand-SKU scale. Persists to a single .npz."""

from __future__ import annotations

from pathlib import Path

import numpy as np


class SkuIndex:
    def __init__(self, vectors: np.ndarray, sku_ids: list[str]) -> None:
        self._vectors = vectors  # (N, D) float32, each row L2-normalized
        self._sku_ids = sku_ids

    def __len__(self) -> int:
        return len(self._sku_ids)

    def query(self, vec: np.ndarray, k: int) -> list[tuple[str, float]]:
        if not self._sku_ids:
            return []
        sims = self._vectors @ vec  # cosine, both sides normalized
        order = np.argsort(-sims)[:k]
        return [(self._sku_ids[i], float(sims[i])) for i in order]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, vectors=self._vectors, ids=np.array(self._sku_ids))

    @classmethod
    def load(cls, path: Path) -> SkuIndex:
        data = np.load(path)
        return cls(data["vectors"], [str(x) for x in data["ids"]])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd brain && .venv/bin/python -m pytest tests/test_sku_index.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Lint**

Run: `cd brain && .venv/bin/ruff check src/glimpse_brain/sku/index.py tests/test_sku_index.py`

- [ ] **Step 7: Commit**

```
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/src/glimpse_brain/sku/__init__.py brain/src/glimpse_brain/sku/index.py brain/tests/test_sku_index.py
git commit -m "feat(sku): numpy cosine SkuIndex + npz persistence"
```

---

## Task 2: `SkuEmbedder` (Pillow preprocess + ONNX embed) + Pillow dep

**Files:**
- Modify: `brain/pyproject.toml`
- Create: `brain/src/glimpse_brain/sku/embedder.py`
- Test: `brain/tests/test_sku_embedder.py`

- [ ] **Step 1: Declare and install Pillow**

In `brain/pyproject.toml`, add `pillow>=10` to the `dependencies` list (currently `["pydantic>=2.7", "anthropic>=0.40", "mempalace==3.4.0", "pyyaml>=6"]` → append `"pillow>=10"`).

Then install it into the venv:
Run: `cd brain && .venv/bin/pip install "pillow>=10"`
Expected: Pillow installs successfully (or "already satisfied").

- [ ] **Step 2: Write the failing test (preprocessing only — the ONNX embed is the integration seam)**

Create `brain/tests/test_sku_embedder.py`:
```python
from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from glimpse_brain.sku.embedder import SkuEmbedder


def jpeg_bytes(color: tuple[int, int, int] = (200, 100, 50)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), color).save(buf, format="JPEG")
    return buf.getvalue()


def test_preprocess_shape_and_dtype() -> None:
    # _preprocess is pure (no ONNX session needed) — call it on the class via
    # __new__ to avoid loading a model.
    embedder = SkuEmbedder.__new__(SkuEmbedder)
    arr = embedder._preprocess(jpeg_bytes())
    assert arr.shape == (1, 3, 224, 224)
    assert arr.dtype == np.float32
    # CLIP normalization puts values well outside [0,1]; a flat color image must
    # not be all zeros after (x-mean)/std.
    assert not np.allclose(arr, 0.0)


def test_preprocess_handles_grayscale_and_rgba() -> None:
    embedder = SkuEmbedder.__new__(SkuEmbedder)
    for mode in ("L", "RGBA"):
        buf = io.BytesIO()
        Image.new(mode, (64, 64)).save(buf, format="PNG")
        arr = embedder._preprocess(buf.getvalue())
        assert arr.shape == (1, 3, 224, 224)  # converted to 3-channel RGB


def test_preprocess_rejects_garbage_bytes() -> None:
    embedder = SkuEmbedder.__new__(SkuEmbedder)
    with pytest.raises(Exception):
        embedder._preprocess(b"not an image")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd brain && .venv/bin/python -m pytest tests/test_sku_embedder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'glimpse_brain.sku.embedder'`.

- [ ] **Step 4: Implement `SkuEmbedder`**

Create `brain/src/glimpse_brain/sku/embedder.py`:
```python
"""CLIP image-encoder embeddings via onnxruntime (no torch at inference). The
single home of Chinese-CLIP's preprocessing (224px bicubic + CLIP mean/std).
The ONNX session is created lazily so _preprocess is unit-testable without a model."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

# OpenAI/CLIP normalization constants (Chinese-CLIP uses the same).
_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


class SkuEmbedder:
    def __init__(self, model_path: Path) -> None:
        self._session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name

    def _preprocess(self, jpeg_bytes: bytes) -> np.ndarray:
        img = (
            Image.open(io.BytesIO(jpeg_bytes))
            .convert("RGB")
            .resize((224, 224), Image.BICUBIC)
        )
        arr = np.asarray(img, dtype=np.float32) / 255.0  # (224,224,3)
        arr = (arr - _MEAN) / _STD
        arr = arr.transpose(2, 0, 1)[None, :, :, :]  # (1,3,224,224)
        return np.ascontiguousarray(arr, dtype=np.float32)

    def embed(self, jpeg_bytes: bytes) -> np.ndarray:
        x = self._preprocess(jpeg_bytes)
        out = self._session.run(None, {self._input_name: x})[0]  # (1, D)
        vec = np.asarray(out[0], dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 0 else vec
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd brain && .venv/bin/python -m pytest tests/test_sku_embedder.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Lint**

Run: `cd brain && .venv/bin/ruff check src/glimpse_brain/sku/embedder.py tests/test_sku_embedder.py`

- [ ] **Step 7: Commit**

```
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/pyproject.toml brain/src/glimpse_brain/sku/embedder.py brain/tests/test_sku_embedder.py
git commit -m "feat(sku): SkuEmbedder (CLIP preprocess + onnx embed) + pillow dep"
```

---

## Task 3: `SkuMatcher` facade

**Files:**
- Create: `brain/src/glimpse_brain/sku/matcher.py`
- Test: `brain/tests/test_sku_matcher.py`

**Scene:** `SkuMatcher` ties an embedder (anything with `.embed(jpeg_bytes) -> np.ndarray`) to a `SkuIndex`, and applies `top_k`/`min_score`. Tests use a fake embedder — no ONNX.

- [ ] **Step 1: Write the failing tests**

Create `brain/tests/test_sku_matcher.py`:
```python
from __future__ import annotations

import numpy as np

from glimpse_brain.sku.index import SkuIndex
from glimpse_brain.sku.matcher import SkuMatcher


def unit(*vals: float) -> np.ndarray:
    v = np.array(vals, dtype=np.float32)
    return v / np.linalg.norm(v)


class FakeEmbedder:
    """Maps specific jpeg payloads to fixed vectors."""

    def __init__(self, vec: np.ndarray) -> None:
        self._vec = vec

    def embed(self, jpeg_bytes: bytes) -> np.ndarray:
        return self._vec


def index() -> SkuIndex:
    return SkuIndex(np.stack([unit(1, 0), unit(0, 1), unit(1, 1)]), ["east", "north", "ne"])


def test_match_returns_top_k_by_cosine() -> None:
    m = SkuMatcher(FakeEmbedder(unit(1, 0.1)), index(), top_k=2, min_score=0.0)
    hits = m.match(b"photo")
    assert [sku for sku, _ in hits] == ["east", "ne"]


def test_match_filters_below_min_score() -> None:
    # query orthogonal-ish to "north" → that score is low and filtered out.
    m = SkuMatcher(FakeEmbedder(unit(1, 0)), index(), top_k=3, min_score=0.6)
    skus = [sku for sku, _ in m.match(b"photo")]
    assert "north" not in skus  # cosine(east-query, north) == 0 < 0.6
    assert "east" in skus
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && .venv/bin/python -m pytest tests/test_sku_matcher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'glimpse_brain.sku.matcher'`.

- [ ] **Step 3: Implement `SkuMatcher`**

Create `brain/src/glimpse_brain/sku/matcher.py`:
```python
"""Facade: embed a photo, query the index, apply top_k/min_score. The agent's
per-turn SKU dependency (built once at startup)."""

from __future__ import annotations

from typing import Protocol

import numpy as np

from glimpse_brain.sku.index import SkuIndex


class _Embedder(Protocol):
    def embed(self, jpeg_bytes: bytes) -> np.ndarray: ...


class SkuMatcher:
    def __init__(
        self, embedder: _Embedder, index: SkuIndex, top_k: int = 5, min_score: float = 0.0
    ) -> None:
        self._embedder = embedder
        self._index = index
        self._top_k = top_k
        self._min_score = min_score

    def match(self, jpeg_bytes: bytes) -> list[tuple[str, float]]:
        vec = self._embedder.embed(jpeg_bytes)
        hits = self._index.query(vec, self._top_k)
        return [(sku, score) for sku, score in hits if score >= self._min_score]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && .venv/bin/python -m pytest tests/test_sku_matcher.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Lint**

Run: `cd brain && .venv/bin/ruff check src/glimpse_brain/sku/matcher.py tests/test_sku_matcher.py`

- [ ] **Step 6: Commit**

```
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/src/glimpse_brain/sku/matcher.py brain/tests/test_sku_matcher.py
git commit -m "feat(sku): SkuMatcher facade (embed + query + top_k/min_score)"
```

---

## Task 4: `MatchSkuTool` (`match_sku`)

**Files:**
- Create: `brain/src/glimpse_brain/sku/tool.py`
- Test: `brain/tests/test_sku_tool.py`

**Scene:** Mirrors `vision_tool.LookTool`: built per-turn with the held base64 image. `run()` base64-decodes the image, calls `matcher.match`, formats the top-k. Fail-soft text on no-image / runtime error / empty matches.

- [ ] **Step 1: Write the failing tests**

Create `brain/tests/test_sku_tool.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && .venv/bin/python -m pytest tests/test_sku_tool.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'glimpse_brain.sku.tool'`.

- [ ] **Step 3: Implement `MatchSkuTool`**

Create `brain/src/glimpse_brain/sku/tool.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && .venv/bin/python -m pytest tests/test_sku_tool.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Lint**

Run: `cd brain && .venv/bin/ruff check src/glimpse_brain/sku/tool.py tests/test_sku_tool.py`

- [ ] **Step 6: Commit**

```
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/src/glimpse_brain/sku/tool.py brain/tests/test_sku_tool.py
git commit -m "feat(sku): match_sku tool (fail-soft top-k → read_knowledge)"
```

---

## Task 5: Offline `build` CLI

**Files:**
- Create: `brain/src/glimpse_brain/sku/build.py`
- Test: `brain/tests/test_sku_build.py`

**Scene:** `build_index(image_dir, embedder)` is the pure, testable core (fake embedder); `_main` wires the real `SkuEmbedder` and handles argv/errors. Filename stem = SKU id.

- [ ] **Step 1: Write the failing tests**

Create `brain/tests/test_sku_build.py`:
```python
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

from glimpse_brain.sku.build import build_index


def write_png(path: Path, color: tuple[int, int, int] = (10, 20, 30)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), color).save(path, format="PNG")


class CountingEmbedder:
    """Deterministic per-call vector; records how many images it embedded."""

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, jpeg_bytes: bytes) -> np.ndarray:
        self.calls += 1
        v = np.array([float(len(jpeg_bytes)), float(self.calls)], dtype=np.float32)
        return v / np.linalg.norm(v)


def test_build_index_uses_filename_stem_as_id(tmp_path: Path) -> None:
    write_png(tmp_path / "example-a.png")
    write_png(tmp_path / "shipping.jpg")
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")  # non-image
    embedder = CountingEmbedder()
    index = build_index(tmp_path, embedder)
    assert len(index) == 2  # txt skipped
    assert embedder.calls == 2
    # ids are the filename stems, sorted
    hits = index.query(np.array([0.0, 1.0], dtype=np.float32), k=2)
    assert {sku for sku, _ in hits} == {"example-a", "shipping"}


def test_build_index_empty_dir(tmp_path: Path) -> None:
    index = build_index(tmp_path, CountingEmbedder())
    assert len(index) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && .venv/bin/python -m pytest tests/test_sku_build.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'glimpse_brain.sku.build'`.

- [ ] **Step 3: Implement `build.py`**

Create `brain/src/glimpse_brain/sku/build.py`:
```python
"""Offline: build the SKU visual index from a directory of product images.
Run: python -m glimpse_brain.sku.build <image_dir> <out.npz> [--model PATH]
Filename stem = SKU id (= the OKF product doc id). Local compute, no API."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Protocol

import numpy as np

from glimpse_brain.sku.index import SkuIndex

_EXTS = {".jpg", ".jpeg", ".png"}


class _Embedder(Protocol):
    def embed(self, jpeg_bytes: bytes) -> np.ndarray: ...


def build_index(image_dir: Path, embedder: _Embedder) -> SkuIndex:
    paths = sorted(p for p in image_dir.glob("*") if p.suffix.lower() in _EXTS)
    vectors: list[np.ndarray] = []
    ids: list[str] = []
    for path in paths:
        try:
            vec = embedder.embed(path.read_bytes())
        except Exception as exc:  # one bad image must not abort the build
            print(f"[skip] {path.name}: {exc}", file=sys.stderr)
            continue
        vectors.append(vec)
        ids.append(path.stem)
    if not vectors:
        return SkuIndex(np.zeros((0, 0), dtype=np.float32), [])
    return SkuIndex(np.stack(vectors).astype(np.float32), ids)


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m glimpse_brain.sku.build")
    parser.add_argument("image_dir", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("~/.glimpse/sku/cnclip_vitb16.img.onnx").expanduser(),
    )
    args = parser.parse_args(argv)
    if not args.image_dir.is_dir():
        print(f"image dir not found: {args.image_dir}", file=sys.stderr)
        return 2
    if not args.model.exists():
        print(
            f"model not found: {args.model}\n"
            "（请先将 Chinese-CLIP ViT-B/16 图像编码器导出为 ONNX 放到该路径）",
            file=sys.stderr,
        )
        return 2
    from glimpse_brain.sku.embedder import SkuEmbedder

    index = build_index(args.image_dir, SkuEmbedder(args.model))
    index.save(args.out)
    print(f"indexed {len(index)} SKUs → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && .venv/bin/python -m pytest tests/test_sku_build.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Create the opt-in integration test (real ONNX seam)**

Create `brain/tests/test_sku_integration.py`. It is marked `integration`, so the default suite (`addopts = -m 'not integration'`) never collects it; and even under `-m integration` it SKIPS unless the four env vars point to a real model + images. This is the file the README's verify step runs.
```python
from __future__ import annotations

import os
from pathlib import Path

import pytest

from glimpse_brain.sku.build import build_index
from glimpse_brain.sku.embedder import SkuEmbedder

pytestmark = pytest.mark.integration

_MODEL = os.environ.get("SKU_MODEL_PATH")
_IMAGES = os.environ.get("SKU_IMAGE_DIR")
_QUERY = os.environ.get("SKU_QUERY_IMAGE")
_EXPECTED = os.environ.get("SKU_EXPECTED_ID")


@pytest.mark.skipif(
    not (_MODEL and _IMAGES and _QUERY and _EXPECTED),
    reason="set SKU_MODEL_PATH, SKU_IMAGE_DIR, SKU_QUERY_IMAGE, SKU_EXPECTED_ID to run",
)
def test_real_clip_matches_expected_sku() -> None:
    embedder = SkuEmbedder(Path(_MODEL))
    index = build_index(Path(_IMAGES), embedder)
    assert len(index) > 0
    vec = embedder.embed(Path(_QUERY).read_bytes())
    hits = index.query(vec, k=3)
    assert _EXPECTED in [sku for sku, _ in hits]
```

Verify it is excluded from the default run (collected = 0 under default opts):
Run: `cd brain && .venv/bin/python -m pytest tests/test_sku_integration.py -q`
Expected: `no tests ran` / deselected (the `integration` marker excludes it). Then confirm it SKIPS (not errors) when explicitly selected without env:
Run: `cd brain && .venv/bin/python -m pytest tests/test_sku_integration.py -m integration -q`
Expected: `1 skipped` (the skipif reason).

- [ ] **Step 6: Lint**

Run: `cd brain && .venv/bin/ruff check src/glimpse_brain/sku/build.py tests/test_sku_build.py tests/test_sku_integration.py`

- [ ] **Step 7: Commit**

```
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/src/glimpse_brain/sku/build.py brain/tests/test_sku_build.py brain/tests/test_sku_integration.py
git commit -m "feat(sku): offline build CLI + opt-in real-ONNX integration test"
```

---

## Task 6: Config — `SkuCfg`

**Files:**
- Modify: `brain/src/glimpse_brain/config.py`
- Test: `brain/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `brain/tests/test_config.py`:
```python
def test_sku_cfg_defaults_and_expand() -> None:
    from glimpse_brain.config import Config

    cfg = Config()
    assert cfg.sku.enabled is True
    assert cfg.sku.top_k == 5
    assert cfg.sku.min_score == 0.0
    assert cfg.sku.model_path.endswith("/.glimpse/sku/cnclip_vitb16.img.onnx")
    assert cfg.sku.index_path.endswith("/.glimpse/sku/index.npz")
    assert "~" not in cfg.sku.model_path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd brain && .venv/bin/python -m pytest tests/test_config.py -k sku_cfg -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'sku'`.

- [ ] **Step 3: Add `SkuCfg` and wire into `Config`**

In `brain/src/glimpse_brain/config.py`, add a new model after `FeedbackCfg` (note `protected_namespaces=()` — `model_path` would otherwise collide with pydantic's protected `model_` namespace, same as `LlmCfg` does for its `model` field):
```python
class SkuCfg(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    enabled: bool = True
    model_path: str = Field(
        default_factory=lambda: str(Path("~/.glimpse/sku/cnclip_vitb16.img.onnx").expanduser())
    )
    index_path: str = Field(
        default_factory=lambda: str(Path("~/.glimpse/sku/index.npz").expanduser())
    )
    top_k: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("model_path", "index_path", mode="before")
    @classmethod
    def _expand(cls, v: str) -> str:
        return str(Path(v).expanduser())
```

Add the field to `Config` (after `feedback`):
```python
    sku: SkuCfg = Field(default_factory=SkuCfg)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd brain && .venv/bin/python -m pytest tests/test_config.py -k sku_cfg -v`
Expected: PASS.

- [ ] **Step 5: Run the full config file + lint**

Run: `cd brain && .venv/bin/python -m pytest tests/test_config.py -q`
Run: `cd brain && .venv/bin/ruff check src/glimpse_brain/config.py tests/test_config.py`

- [ ] **Step 6: Commit**

```
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/src/glimpse_brain/config.py brain/tests/test_config.py
git commit -m "feat(sku): SkuCfg config"
```

---

## Task 7: Agent registers `match_sku` per-turn

**Files:**
- Modify: `brain/src/glimpse_brain/agent.py`
- Test: `brain/tests/test_agent.py`

**Scene:** `Agent.suggest` builds per-turn tools: base + memory (if customer) + `LookTool` (if image). Add: + `MatchSkuTool` (if image AND a `SkuMatcher` is present). `Agent.__init__` gains `sku: SkuMatcher | None = None`. A `SkuMatcher` is anything with `.match(jpeg_bytes) -> list[tuple[str, float]]` — tests inject a fake.

- [ ] **Step 1: Write the failing test**

Add to `brain/tests/test_agent.py` (the file already imports `Agent`, `AgentStep`, `ToolCall`, `Redactor`, `RateLimiter`, and defines `FakeKB`/`ScriptedClient`/`make_agent`):
```python
async def test_agent_matches_sku_then_reads_doc() -> None:
    import base64

    from glimpse_brain.tooluse import AgentStep, ToolCall

    class FakeMatcher:
        def __init__(self) -> None:
            self.calls = 0

        def match(self, jpeg_bytes: bytes):
            self.calls += 1
            return [("example-a", 0.82)]

    matcher = FakeMatcher()
    client = ScriptedClient([
        AgentStep(tool_calls=(ToolCall(id="m1", name="match_sku", input={}),)),
        AgentStep(tool_calls=(ToolCall(id="r1", name="read_knowledge", input={"id": "example-a"}),)),
        AgentStep(final_text='["这是示例商品A，亲～"]'),
    ])
    agent = Agent(
        client=client, system="SYS", knowledge=FakeKB(),
        redactor=Redactor([]), limiter=RateLimiter(10),
        max_suggestions=3, max_iterations=5, sku=matcher,
    )
    img = base64.b64encode(b"jpeg").decode()
    result = await agent.suggest(["客户: 这是什么型号"], image=img)
    assert matcher.calls == 1  # match_sku actually executed (registered)
    assert "match_sku" in result.tools_used
    assert result.drafts == ["这是示例商品A，亲～"]


async def test_agent_omits_match_sku_without_matcher() -> None:
    import base64

    from glimpse_brain.tooluse import AgentStep

    captured = {}

    class CaptureToolsClient:
        async def run_turn(self, *, system, transcript, tools) -> AgentStep:
            captured["tool_names"] = [t.name for t in tools]
            return AgentStep(final_text='["在的"]')

    agent = Agent(
        client=CaptureToolsClient(), system="SYS", knowledge=FakeKB(),
        redactor=Redactor([]), limiter=RateLimiter(10),
        max_suggestions=3, max_iterations=4, sku=None,
    )
    await agent.suggest(["客户: 在吗"], image=base64.b64encode(b"jpeg").decode())
    assert "match_sku" not in captured["tool_names"]  # no matcher → not offered
    assert "look_at_conversation" in captured["tool_names"]  # but vision still is
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && .venv/bin/python -m pytest tests/test_agent.py -k "match_sku" -v`
Expected: FAIL — `Agent.__init__` has no `sku` parameter (TypeError), and `match_sku` is never registered.

- [ ] **Step 3: Add the `sku` dependency and per-turn tool**

In `brain/src/glimpse_brain/agent.py`:

(a) Add the import (near the `LookTool` import):
```python
from glimpse_brain.sku.matcher import SkuMatcher
from glimpse_brain.sku.tool import MatchSkuTool
```

(b) Add the constructor parameter. The existing signature ends with `memory: Memory | None = None, recall_k: int = 5`. Add `sku: SkuMatcher | None = None,` to the keyword-only params, and store it: `self._sku = sku` (place it next to `self._memory = memory`).

(c) In `suggest`, after the existing `if image: tools.append(LookTool(image))` block, add:
```python
        if image and self._sku is not None:
            tools.append(MatchSkuTool(self._sku, image))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && .venv/bin/python -m pytest tests/test_agent.py -k "match_sku" -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the whole agent file + lint**

Run: `cd brain && .venv/bin/python -m pytest tests/test_agent.py -q`
Expected: all PASS.
Run: `cd brain && .venv/bin/ruff check src/glimpse_brain/agent.py tests/test_agent.py`

- [ ] **Step 6: Commit**

```
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/src/glimpse_brain/agent.py brain/tests/test_agent.py
git commit -m "feat(sku): agent registers match_sku when image + matcher present"
```

---

## Task 8: Server `_build_sku` + wiring + `AGENT_SYSTEM`

**Files:**
- Modify: `brain/src/glimpse_brain/server.py`
- Test: `brain/tests/test_server.py`

**Scene:** `GlimpseServer.__init__` builds the `Agent`. It already has `_build_memory(cfg) -> Memory | None`. Add a parallel `_build_sku(cfg) -> SkuMatcher | None` (fail-soft load of embedder+index), pass it to `Agent(sku=...)`, and add a `match_sku` line to `AGENT_SYSTEM`. Server tests disable SKU via config so they stay hermetic.

- [ ] **Step 1: Update `make_config` and add tests in `test_server.py`**

In `brain/tests/test_server.py`, add `"sku": {"enabled": False}` as a top-level key in `make_config`'s validated dict (a sibling of `"brain"`, `"tracker"`, `"memory"`), so server tests never touch `~/.glimpse/sku`:
```python
            "memory": {"enabled": False},
            "sku": {"enabled": False},
```
(Add it alongside the existing `"memory"` entry; keep all other keys.)

Then append these tests to `brain/tests/test_server.py`:
```python
def test_build_sku_disabled_returns_none(tmp_path: Path) -> None:
    from glimpse_brain.server import GlimpseServer

    cfg = make_config(tmp_path)  # sku disabled
    assert GlimpseServer._build_sku(cfg) is None


def test_build_sku_missing_files_returns_none(tmp_path: Path) -> None:
    from glimpse_brain.config import Config
    from glimpse_brain.server import GlimpseServer

    cfg = Config.model_validate(
        {
            "sku": {
                "enabled": True,
                "model_path": str(tmp_path / "nope.onnx"),
                "index_path": str(tmp_path / "nope.npz"),
            }
        }
    )
    # Missing model/index → load raises → fail-soft to None (SKU disabled).
    assert GlimpseServer._build_sku(cfg) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && .venv/bin/python -m pytest tests/test_server.py -k build_sku -v`
Expected: FAIL — `GlimpseServer` has no `_build_sku`.

- [ ] **Step 3: Add `_build_sku`, wire it, and update `AGENT_SYSTEM`**

In `brain/src/glimpse_brain/server.py`:

(a) Add a `match_sku` line to the `AGENT_SYSTEM` string — append it right after the existing `look_at_conversation` line:
```
当客户发来商品/售后图片、需要确认是哪款商品时，调用 match_sku 获取候选 SKU，再用 read_knowledge 查看候选商品文档确认。
```

(b) Add a static `_build_sku` method next to the existing `_build_memory` static method:
```python
    @staticmethod
    def _build_sku(cfg: Config):  # -> SkuMatcher | None
        if not cfg.sku.enabled:
            return None
        try:
            from glimpse_brain.sku.embedder import SkuEmbedder
            from glimpse_brain.sku.index import SkuIndex
            from glimpse_brain.sku.matcher import SkuMatcher

            embedder = SkuEmbedder(Path(cfg.sku.model_path))
            index = SkuIndex.load(Path(cfg.sku.index_path))
            return SkuMatcher(
                embedder, index, top_k=cfg.sku.top_k, min_score=cfg.sku.min_score
            )
        except Exception:  # missing/broken model or index → SKU matching disabled
            log.exception("sku matching disabled: build failed")
            return None
```

(c) In `__init__`, build the matcher and pass it to the `Agent`. After the line that sets `self._memory = ...` (or near it), add:
```python
        self._sku = self._build_sku(cfg)
```
and in the `Agent(...)` construction add the argument (alongside `memory=self._memory`):
```python
            sku=self._sku,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && .venv/bin/python -m pytest tests/test_server.py -k build_sku -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the FULL brain suite + lint**

Run: `cd brain && .venv/bin/python -m pytest -q`
Expected: ALL pass (1 integration deselected).
Run: `cd brain && .venv/bin/ruff check src tests`
Expected: All checks passed.

- [ ] **Step 6: Commit**

```
cd /Users/john/Projects/visual-habit-tracking-agent
git add brain/src/glimpse_brain/server.py brain/tests/test_server.py
git commit -m "feat(sku): server _build_sku (fail-soft) + wire match_sku + AGENT_SYSTEM"
```

---

## Task 9: Docs — model export, build, naming convention

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the SKU matching section**

In `README.md`, after the `## Knowledge base — OKF catalog` section, add:
```markdown
## SKU visual matching

When a customer sends a product/售后 photo, the agent can call `match_sku` to find
the nearest catalog SKUs (CLIP image embeddings via onnxruntime — no torch), then
`read_knowledge` on the candidate ids to confirm and reply. Fail-soft: if the model
or index is absent, the agent falls back to describing the photo (pure P6).

**One-time model setup** (the ONNX file is not committed — it's large):
1. Export the Chinese-CLIP ViT-B/16 **image** encoder to ONNX (see the Chinese-CLIP
   repo's deployment guide, or download a pre-exported ONNX), and place it at
   `~/.glimpse/sku/cnclip_vitb16.img.onnx`.

**Build the index** from your product images (filename stem = SKU id = the OKF
product doc id, e.g. `example-a.jpg` ↔ `playbook/knowledge/example-a.md`):
```bash
cd brain
.venv/bin/python -m glimpse_brain.sku.build /path/to/product-images ~/.glimpse/sku/index.npz
```

Config (`sku` table in `~/.glimpse/glimpse.toml`): `enabled`, `model_path`,
`index_path`, `top_k` (default 5), `min_score` (default 0.0).

**Verify end-to-end** (opt-in, needs the real model + a few product images):
```bash
cd brain && .venv/bin/python -m pytest tests/test_sku_integration.py -m integration -v
```
```

- [ ] **Step 2: Commit**

```
cd /Users/john/Projects/visual-habit-tracking-agent
git add README.md
git commit -m "docs: SKU visual matching setup (model export, build, naming)"
```

---

## Final verification (after all tasks)

- [ ] **Full brain suite green:** `cd brain && .venv/bin/python -m pytest -q` — all pass, 1 integration deselected.
- [ ] **Ruff clean:** `cd brain && .venv/bin/ruff check src tests`.
- [ ] **Shell untouched but still green:** `cd shell && swift test 2>&1 | tail -3`.
- [ ] **Fail-soft sanity:** with no model/index present (default dev box), `cd brain && .venv/bin/python -c "from glimpse_brain.config import Config; from glimpse_brain.server import GlimpseServer; print('sku matcher:', GlimpseServer._build_sku(Config()))"` prints `sku matcher: None` (SKU disabled, suggestions unaffected) — confirming the agent degrades to P6 when the catalog isn't built.
- [ ] **Pillow declared:** `grep -n pillow brain/pyproject.toml`.

Note: the real CN-CLIP embedding path (`SkuEmbedder.embed` end-to-end and the index `build` against real images) is the deliberate manual/integration seam — it cannot be verified without the ONNX model and product images, which live outside the repo. Task 5 creates `test_sku_integration.py` (marked `integration`, skipped by default and skipped-not-errored unless `SKU_MODEL_PATH`/`SKU_IMAGE_DIR`/`SKU_QUERY_IMAGE`/`SKU_EXPECTED_ID` are set); the user runs it locally once the model + product images are in place.
```
