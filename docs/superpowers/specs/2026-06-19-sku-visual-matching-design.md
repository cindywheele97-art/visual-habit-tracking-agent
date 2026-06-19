# SKU Visual Matching (Design)

**Date:** 2026-06-19
**Status:** Approved (pending spec review)
**Keystone:** `2026-06-14-ai-native-architecture.md` (P6b — the richer multimodal increment)
**Builds on:** P6 vision (the agent already holds the conversation image and can
`look_at_conversation`) and the OKF knowledge catalog (matched SKU id = an OKF
product doc id the agent reads).

## Goal

Give the agent the ability to identify **which product** a customer photo shows.
P6 gave it *eyes* (it can see the thumbnail); it still can't map that photo to a
specific catalog SKU — the acute 售后 gap. This adds an offline-built **visual
index** over the product images and a `match_sku` tool: the agent embeds the
customer photo, gets the top-k nearest SKUs, then reads those candidates' OKF docs
and disambiguates. Photo → SKU id → OKF product doc → grounded reply.

## Design Principles

- **Reuse the existing ONNX stack — no torch at inference.** The env already runs
  embeddings via `onnxruntime` (+ `numpy`), deliberately avoiding PyTorch. The SKU
  embedder is a CLIP image encoder exported to ONNX, run the same way.
- **Agent disambiguates.** `match_sku` returns top-k candidates (not one forced
  answer); the agent reads their OKF docs and decides — robust to messy 售后 photos.
- **The linkage is the OKF catalog.** A SKU id *is* an OKF product doc id, so the
  agent follows a match with `read_knowledge{id}` — reusing the prior increment.
- **Fail-soft to P6.** Every failure rung (disabled, model/index missing, runtime
  error, low scores) degrades to "the agent describes the photo it can see" — never
  an error on the suggestion path.
- **Offline build, runtime query.** Embedding the catalog is a one-time offline
  command; runtime is pure onnxruntime + numpy nearest-neighbor.

## Scope

### In scope
- A `sku/` subpackage: `SkuEmbedder` (CLIP ONNX), `SkuIndex` (numpy cosine NN),
  `SkuMatcher` (facade), `MatchSkuTool` (`match_sku`), `build` (offline CLI).
- Agent registers `match_sku` per-turn when an image is held and a matcher exists.
- `server._build_sku` (fail-soft load); `SkuCfg` config; `AGENT_SYSTEM` one line.
- `Pillow` dependency (image decode/resize). README: obtaining the CN-CLIP ONNX +
  building the index + the filename-stem = SKU-id = OKF-doc-id convention.

### Out of scope (YAGNI / deferred)
- **Re-rank by Claude vision** over candidate reference images (the explicit hybrid
  — the top-k + agent-read already disambiguates; add only if accuracy demands).
- **A vector DB / faiss** — brute-force numpy is ample at a few-thousand scale.
- **Text→image search** of the catalog (description → SKU). Image→image only.
- **Auto-export of the ONNX model** — obtained once via CN-CLIP's script/ModelScope
  and placed at a config path; not committed (large), like the embeddinggemma model.
- **Committing product images or the index** to the repo.
- **Build-time validation that stems match OKF doc ids** — documented convention;
  validation can come later.

## Architecture

### Embedding stack
- **Model:** Chinese-CLIP **ViT-B/16 image encoder → ONNX** (512-dim, MIT,
  domain-tuned for Chinese e-commerce). Inference via `onnxruntime`, no torch.
- **Runtime deps:** `onnxruntime` + `numpy` (present) + **`Pillow`** (new): decode
  the base64 JPEG → RGB → CN-CLIP's 224px bicubic resize + CLIP mean/std normalize.

### Units (`brain/src/glimpse_brain/sku/`)
| Unit | Kind | Responsibility |
|---|---|---|
| `embedder.py: SkuEmbedder` | new | `__init__(model_path)` loads the ONNX session. `_preprocess(jpeg_bytes) -> np.ndarray` (Pillow → `(1,3,224,224)` float32, CLIP-normalized). `embed(jpeg_bytes) -> np.ndarray` runs the session → L2-normalized 512-d vector. The single home of CN-CLIP preprocessing. |
| `index.py: SkuIndex` | new | Holds `vectors: np.ndarray (N,512)` + `sku_ids: list[str]`. `query(vec, k) -> list[tuple[str, float]]` cosine top-k (brute-force `vectors @ vec`, both L2-normalized). `save(path)`/`load(path)` an `.npz` (`vectors`, `ids`). Empty index → `[]`. |
| `matcher.py: SkuMatcher` | new | Facade over embedder + index: `match(jpeg_bytes, k) -> list[tuple[str, float]]`. Built once at startup; the agent's per-turn dependency. |
| `tool.py: MatchSkuTool` | new | `match_sku`. Built per turn with `(matcher, image_b64)`. `run()` → `matcher.match` → formatted top-k text; friendly fallback when no usable match. |
| `build.py` (`python -m glimpse_brain.sku.build`) | new | Offline: walk an image dir, embed each, save the `.npz`. Filename stem = SKU id. |

### Tool surface & data flow
`match_sku` takes no required args (optional `k`); it uses the held conversation
image (the one P6 stores as `server._last_image`).

```
startup: SkuMatcher = _build_sku(cfg)        # load ONNX embedder + index.npz; None on any failure
turn: tools = base(+memory if customer)(+ LookTool if image)(+ MatchSkuTool if image AND matcher)
  agent → match_sku → embed(photo) → index.query(k) →
        "候选商品：\n- example-a（相似度 0.82）\n- example-b（相似度 0.74）；用 read_knowledge 查看确认。"
  agent → read_knowledge{id: "example-a"} → OKF product doc → grounded reply
```

### Wiring (mirrors the Memory pattern)
- `Agent.__init__(..., sku: SkuMatcher | None = None)`; per turn
  `if image and self._sku is not None: tools.append(MatchSkuTool(self._sku, image))`.
- `server.py`: `_build_sku(cfg) -> SkuMatcher | None` (try/except → `None`); passed to `Agent`.
- `AGENT_SYSTEM` += 当客户发来商品/售后图片、需要确认是哪款商品时，调用 match_sku
  获取候选 SKU，再用 read_knowledge 查看候选商品文档确认。
- `config.py`: `SkuCfg{enabled: bool = True, model_path, index_path, top_k: int = 5, min_score: float = 0.0}`,
  paths default under `~/.glimpse/sku/` (`cnclip_vitb16.img.onnx`, `index.npz`). `_expand` the paths.

### Offline build
`python -m glimpse_brain.sku.build <image_dir> <out.npz> [--model PATH]`:
walk `*.jpg/*.jpeg/*.png` (stem = SKU id), embed each, `SkuIndex(...).save(out)`.
Print the count. Local compute, no API.

## Error Handling (every rung degrades to P6 "agent describes the photo")

| Condition | Behavior |
|---|---|
| `sku.enabled=False`, or model/index file missing, or load fails | `SkuMatcher=None` → `match_sku` not registered; `look_at_conversation` still available |
| No current image | `match_sku` not offered (same gate as `LookTool`) |
| `embed`/`query` raises at runtime | tool returns `（商品识别暂不可用）`; agent proceeds on the visible photo |
| All matches below `min_score` | `（未找到相近商品）`; agent describes/asks rather than guess |
| build: missing dir | error and exit (explicit offline op) |
| build: undecodable image | skip + warn, continue |
| build: model file absent | clear error explaining how to obtain the CN-CLIP ONNX |

## Testing (pytest, fakes — real ONNX model is an opt-in integration seam)

- **`SkuIndex` (pure numpy):** `query` returns nearest by cosine descending; respects
  `k`; `save`/`load` `.npz` round-trips ids + vectors; empty index → `[]`. Synthetic vectors.
- **`SkuEmbedder._preprocess` (pure):** a synthetic Pillow JPEG → `(1,3,224,224)`
  float32 normalized into CLIP's range. The ONNX `embed` itself is the integration seam.
- **`SkuMatcher` (fake embedder + small index):** returns the correct SKU ids by cosine.
- **`MatchSkuTool` (fake matcher):** formats the top-k; no image / all-below-`min_score`
  → the friendly not-found text.
- **`build.py` (fake embedder):** tmp dir of tiny images → `.npz` with stem ids;
  skips non-images; missing dir errors.
- **`Agent` (scripted client + fake `SkuMatcher`):** image + matcher → agent calls
  `match_sku` then `read_knowledge`, `tools_used` has both; no matcher → `match_sku`
  not offered. *Intent: the photo→SKU→OKF path works end-to-end.*
- **`server._build_sku`:** disabled / missing files → `None`; present → a matcher.
- **Opt-in integration (`-m integration`, real ONNX):** build a tiny index from 2–3
  real product images; embed a query image → assert the expected nearest SKU
  (validates CN-CLIP preprocessing end-to-end).

**Coverage target:** ≥80% on the pure units (`SkuIndex`, preprocessing, `SkuMatcher`,
`MatchSkuTool`, `build`, agent/server wiring). Real ONNX embedding is the deliberate
manual/integration seam.

## Dependencies

Add `pillow>=10` to `brain/pyproject.toml`. The CN-CLIP ONNX model file is obtained
once (CN-CLIP export script / ModelScope) and placed at `model_path`; it and the
built index and the product images are **not** committed (large, environment-specific).
README documents the one-time model export, the build command, and the
filename-stem = SKU-id = OKF-doc-id naming convention.
