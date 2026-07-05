# Glimpse 遗留缺陷清偿计划（Backlog Remediation Plan）

> **执行者须知（Cursor）：** 本计划由 Planner（Claude，主线程架构师）制定。你负责按阶段执行代码编写。
> 每个阶段独立分支、独立验收、不达标即回滚。任何偏离本计划的架构级改动必须停下来，把问题带回给 Planner，不允许自行发挥。
> 步骤使用 checkbox（`- [ ]`）跟踪。

**目标：** 清偿全库审查中确认的全部 medium/low 遗留缺陷（critical/high 已在 `7594d35`、`73bc945` 修复），并把测试与文档欠账补齐。

**架构立场（已定，不再讨论）：**
- 双进程架构不变：Swift shell（捕获/OCR/合成输入）↔ NDJSON over UDS ↔ Python brain（agent/记忆/知识）。
- 协议层 `protocol.py` / `Protocol.swift` 是**硬边界**：两侧 `extra="forbid"` / 非可选解码，任何 wire 字段增删必须两侧同步 + 两侧测试，本计划内**没有任何阶段需要改协议**——如果你发现自己在改协议，说明做错了。
- 失败哲学：**基础设施 fail-soft（降级继续跑）、但必须 fail-loud（日志/状态可见）**；安全门 fail-closed。"静默吞错"是本计划要消灭的对象，不许新增。
- 隐私不变量（碰任何相关代码前默读一遍）：截图默认不出本机（`[llm] send_images=false` 门控 LookTool）；发给 LLM 的文本先过 `Redactor`；事件日志只存脱敏文本；点击捕获 fail-closed 于被点击窗口的 owner。

**技术栈与基线（2026-07-03，commit `73bc945`）：**

| 项 | 基线值 | 命令 |
|---|---|---|
| brain 测试 | **195 passed, 2 deselected** | `cd brain && ./.venv/bin/python -m pytest -q` |
| brain lint | **All checks passed** | `cd brain && ./.venv/bin/python -m ruff check src tests` |
| brain 类型 | **10 errors in 7 files**（Phase 6 清零） | `cd brain && ./.venv/bin/python -m mypy` |
| shell 测试 | **61 passed** | `cd shell && swift test` |
| shell 构建 | **Build complete** | `cd shell && swift build` |

## 计划修订机制（Planner 定，取代任何平行冻结副本）

本文件是唯一真相源；git 已在 `fde9c6b` 不可变冻结初版。**禁止**创建 `.BACKUP.md` 或平行 `notes/` 目录——冗余副本必然与 git 版本漂移。需要修订计划时：**由 Planner 直接编辑本文件并提交一个 commit**，message 写清"改了什么/为什么/对下游影响"，`git blame`/`git log` 即完整追溯。Cursor 执行期间**不许改本文件**——发现计划有误，把问题交回 Planner，由 Planner 提交修订。每阶段验收后 Planner 在该阶段末尾追加一条 `✅ 已验收` 记录（含 commit 号）。

## 全局规则（每个阶段隐含包含本节）

1. **分支纪律：** 每阶段 `git checkout -b phase-N-<slug>`（从 main 切出）。阶段内小步提交（conventional commits：`fix:`/`test:`/`refactor:`/`docs:`）。验收全过 → merge 回 main（`--no-ff`）。验收不过且无法在阶段范围内修复 → **放弃分支**（`git checkout main && git branch -D phase-N-<slug>`），把失败原因写成报告交回 Planner，**不许把红的状态 merge 进 main**。
2. **TDD 铁律：** 每个行为变更先写失败测试 → 亲眼看它失败（错误原因必须正确）→ 最小实现 → 全绿。测试要写 WHY 注释（这个仓库的既有风格，见 `brain/tests/test_server.py` 任意一条）。
3. **每阶段开工前跑一遍基线四命令**，数字对不上先停下——说明环境或起点已损坏。
4. **禁改清单（全计划有效）：** `AGENT_SYSTEM`/`build_agent_system` 的语义、`SendPlanner`/`Sender` 的安全门逻辑、tracker 的锚定/append-diff 算法、SettleGate 语义、`IPC_LINE_LIMIT` 机制。这些刚经过多轮对抗审查验证，本计划的任何阶段都不需要动它们。
5. **能力边界（遇到即停，交回 Planner）：** ① 需要改 wire 协议；② 需要 ScreenCaptureKit/辅助功能权限才能写出的测试（单元测试**永远不许**真实调用 SCK 截屏、CGEvent post、Anthropic API、真实 mempalace 模型下载）；③ 两侧接口签名冲突；④ 阶段验收标准本身被发现写错。
6. **已知工具坑（背下来）：**
   - Swift Testing 下 `try #require(optionalClosure)("arg")` 直接调用会**触发编译器断言崩溃**（ConstraintSystem.cpp:3926）。拆两行：`let f = try #require(x); f("arg")`。
   - 测试文件用到 `kCGWindow*` 常量必须 `import CoreGraphics`。
   - brain 测试里 `read_until(reader, "suggestions")` 会**跳过中间消息**；stale 标记与 fresh 建议是两条 `suggestions` 消息，需要的就读两次。断言"某消息不出现"用 `read_next_non_ack`（已存在于 test_server.py）。
   - `HelloMsg` 会清空 `_last_suggestions`（复活防护）——测试里别把 hello 当无副作用的哨兵，用 `{"type":"summarize"}` 作哨兵。
   - fill-echo 过滤器：若测试里客户消息文本 == fake client 返回的草稿文本，stale 标记会被（正确地）跳过。测试数据避免撞车。
   - `make_config`（test_server.py）默认 `memory/sku` 关闭、`settle_ms=30`。
   - 服务器测试模板：`OCR_LINE`（inbound, x0=0.05）、`OCR_OUT_LINE`（outbound, x0=0.62）、`OCR_CONTACT_LINE`（带 contact）都已存在，直接复用。
   - mypy 是 `strict = true`，新代码任何裸 `dict`/缺注解都会新增错误——阶段验收含"mypy 错误数不高于基线"。

---

## Phase 1 — EventLog 可靠性（brain）

**动机（审查确认项）：** ① `EventLog.append` 的 `OSError` 会穿透 `_dispatch` 的窄异常处理，杀死 shell 连接并进入重连循环；② `events.jsonl` 无限增长，无任何轮转。

**模块边界：**
- 可改：`brain/src/glimpse_brain/events.py`、`brain/src/glimpse_brain/config.py`（仅新增 BrainCfg 字段）、`brain/tests/test_events.py`（新建或扩展）、`config/glimpse.toml`。
- 不可改：`server.py` 的调用点签名（`append(kind, region_id, payload)` 保持原样）、`_seed_satisfaction` 的读取逻辑（轮转封顶后其全量读取自然有界）。

**执行要求：**
1. `EventLog.append` 内部捕获 `OSError`：`log.warning`（带路径与异常），事件丢弃，**绝不向调用方抛出**。类 docstring 明确写下契约："append never raises — 审计尾迹的丢失优于杀死整条管线"。
2. 尺寸轮转：`BrainCfg` 新增 `event_log_max_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)`。append 前若当前文件 size 超限 → `os.replace(path, path.with_suffix(".jsonl.1"))`（单代轮转，覆盖旧 `.1`）再写新文件。`EventLog.__init__` 签名变为 `(path, redactor, max_bytes: int = ...)`，`server.py` 构造处传 `cfg.brain.event_log_max_bytes`（这是唯一允许碰 server.py 的一行）。
3. `config/glimpse.toml` 的 `[brain]` 段加注释示例 `event_log_max_bytes`。

**测试契约（先红后绿，各一个 WHY）：**
- `test_append_survives_unwritable_path`：EventLog 指向一个不可写路径（`tmp_path / "no" / "such"` 先 `chmod 0o444` 父目录，或直接把 path 指到一个**目录**上制造 OSError），`append(...)` 不抛异常。
- `test_rotation_caps_file_size`：`max_bytes=200`，连续 append 直到超限，断言主文件被轮转（`.jsonl.1` 存在且主文件重新从小开始），且轮转后 append 的内容能在主文件里读到。
- `test_append_still_writes_redacted`（回归钉）：payload 带手机号 → 落盘内容不含原号码。

**坑点：** ① `chmod` 制造的只读目录在 tmp_path 清理时可能报错——测试结尾恢复权限；② 轮转判断用 `path.stat().st_size`，文件不存在时 `FileNotFoundError` 要当 size=0 处理；③ 别引入后台线程/定时器做轮转——append 时同步判断即可（YAGNI）。

**验收标准：** 基线四命令全绿（brain 测试数 ≥ 197 — Planner 修订：原写 198 系笔误，`test_append_still_writes_redacted` 是既有 `test_payload_is_redacted_before_write` 的重命名而非净增，净增 2 条 → 195+2=197）；`grep -rn "os.fsync" brain/src` 仍存在（fsync 语义保留，本阶段不许动）；新增测试删掉 `try/except OSError` 实现后能变红（Cursor 自查一次再改回来）。

> **✅ Phase 1 已验收并 merge（commit `d0a6a49` / merge `977f3f9`，2026-07-04）。** Gate 记录：197 passed / ruff clean / mypy 10（未新增）/ shell 61 pass；Planner 独立变异测试确认两个新 pin 有牙齿；Cursor 提议的 `BACKUP.md` + `notes/` 平行冻结副本被驳回（git 已冻结 plan，见下条修订规矩）。

---

## Phase 2 — IPC 响应性与断线不丢消息（brain + shell）

**动机：** ① `_on_summarize` 在 `_dispatch` 里内联 await，一次 summarize 阻塞全部 IPC 处理最长 30s；② shell 断线期间所有非 OCR 消息（含不可逆发送的 `RepliedMsg` 审计记录、`FeedbackMsg`）静默丢弃；③ IPCClient 的重连+重发逻辑零测试。

**模块边界：**
- 可改：`brain/src/glimpse_brain/server.py`（仅 `_dispatch` 中 summarize 分支）、`shell/Sources/GlimpseShellLib/IPCClient.swift`、新建 `shell/Sources/GlimpseShellLib/OutboundSpool.swift`、`shell/Tests/GlimpseShellTests/OutboundSpoolTests.swift`、`brain/tests/test_server.py`。
- 不可改：`Wire`/`LineBuffer`、ack 协议语义（seq 匹配即清槽）、OCR 消息的"单槽最新未确认"语义（**不要**把 OCR 也塞进队列——旧帧过时即弃是有意设计）。

**执行要求：**
1. **brain：** `_dispatch` 中 `SummarizeRequest` 分支改为 `asyncio.create_task(self._on_summarize())`，任务引用存到 `self._summarize_task`（防 GC），`_on_summarize` 已自带全量异常处理与 `_summarizing` 去重闸，不需要再包。
2. **shell：** 抽出纯逻辑类型 `OutboundSpool`（无 socket、无 Dispatch 依赖，struct 或 final class）：
   - `mutating func enqueue(_ data: Data)` — 非 OCR 消息入队，容量上限 100 条，满则丢**最旧**并计数；
   - `mutating func setUnacked(_ data: Data, seq: Int)` / `func onAck(_ seq: Int) -> Bool` — 现有单槽 OCR 语义原样搬入；
   - `mutating func drainOnConnect() -> [Data]` — 返回 [未确认 OCR（若有）] + 队列全部（FIFO），队列清空。
   - `IPCClient` 改为持有 `OutboundSpool`：`send` 无 ackSeq → 已连接直接写，未连接（fd<0）→ enqueue；`connect()` 成功后写 `drainOnConnect()` 的每一条。
3. IPCClient 行为不变部分：1s 重连节奏、SO_NOSIGPIPE、LineBuffer 喂给 decode。

**接口契约（后续阶段依赖）：** `OutboundSpool` 是 `GlimpseShellLib` 的 public 类型；`IPCClient.send<T: Codable>(_ msg: T, ackSeq: Int?)` 对外签名不变。

**测试契约：**
- brain：`test_summarize_does_not_block_ocr_processing`——用一个 `complete()` 挂在 `asyncio.Event` 上的 FakeLLM 发起 summarize，随后发 OCR，**在 summarize 完成前**就应收到 OCR 的 ack 与建议；然后放行 event，收到 summary。
- shell（OutboundSpoolTests，全部纯逻辑不碰 socket）：
  - `spoolQueuesWhileDisconnectedAndDrainsFIFOOnConnect`
  - `spoolCapsAtLimitDroppingOldest`（101 条入队 → drain 100 条且是最新的 100）
  - `unackedOcrDrainsBeforeQueuedMessages`（顺序：OCR 重发在前）
  - `ackClearsTheSlotOnlyOnMatchingSeq`（搬运现有语义，防止重构丢行为）

**坑点：** ① `create_task` 的任务若不存引用会被 GC 且异常无人回收——存 `self._summarize_task`；② summarize 任务与连接断开竞态：`_send` 已有 `writer is None` 守卫，无需额外处理；③ Swift 侧一切 spool 状态仍只在 `queue`（glimpse.ipc 串行队列）上碰——保持现有约定，不加锁；④ 不要给 spool 加持久化（YAGNI——断线超过队列容量的丢失打日志即可）。

**验收标准：** 基线四命令全绿（brain ≥ 196，shell ≥ 65）；把 `drainOnConnect` 改成返回空数组后 spool 测试必须变红（自查后改回）；`git grep -n "asyncio.create_task" brain/src/glimpse_brain/server.py` 只应出现在 summarize 分支（SettleGate 内部的 create_task 在 settle.py，不在此文件）。

> **✅ Phase 2 已验收并 merge（commit `00f6b2d` / merge `d0ad431`，2026-07-05）。** Gate 记录：brain 198 / shell 65 / ruff clean / mypy 10（未新增）；Planner 独立变异确认两侧 pin 有牙齿（create_task→await 令 brain 非阻塞测试 TimeoutError；drainOnConnect→[] 令 3/4 spool 测试红）；OutboundSpool 四条路径经 Planner 逐一走查无 bug。**Planner 补一行 fail-loud**：Cursor 实现里 `droppedCount` 有计数却无日志——离线队列丢最旧可能丢掉 RepliedMsg 审计记录，属新增静默失败，违反本批"消灭静默吞错"铁律；已在 IPCClient enqueue 处补 NSLog（不碰接口/测试）。**给 Cursor 的固化要求**：凡"丢弃/截断/降级"路径,一律 fail-loud（日志或状态可见）,勿再需 Planner 提醒。

---

## Phase 3 — 静默失败消除（brain）

**动机：** ① `_recall_sync` 把 mempalace 错误结果静默变成"无记忆"；② `MatchSkuTool.run` 吞掉一切异常不留日志（索引维度不匹配 → SKU 匹配永久静默失效）；③ `load_config` 对显式传入但不存在的 `--config` 路径静默退回全默认；④ `FeedbackCfg` 允许 `satisfaction_window < advisory_min_ratings`（满意度建议永不可达）；⑤ 默认脱敏漏 email 和带分隔符的卡号。

**模块边界：**
- 可改：`mempalace_memory.py`、`sku/tool.py`、`config.py`、`brain/evals/__main__.py`（仅 load_config 调用两处）、`config/glimpse.toml`、对应测试文件。
- 不可改：`Memory` Protocol 签名、`_write_sync` 的 metadata schema（Phase 4 处理 wing）、agent.py 的工具错误包装。

**执行要求（逐项）：**
1. `_recall_sync`：`result` 非 dict 或含 `"error"` 键 → `log.warning("mempalace recall failed: %s", ...)` 后返回 `[]`。加模块级 `log = logging.getLogger("glimpse.memory")`。
2. `MatchSkuTool.run` 的 `except Exception` → 先 `log.exception("match_sku failed")` 再返回既有友好文案（返回值不变，fail-soft 保留）。
3. `load_config(path)`：`path is None` → 默认（不变）；**path 显式给出但不存在 → `raise ValueError(f"config file not found: {path}")`**。同步修 `brain/evals/__main__.py` 两处调用：`p = Path("~/.glimpse/glimpse.toml").expanduser(); cfg = load_config(p if p.exists() else None)`。
4. `FeedbackCfg` 加 `model_validator(mode="after")`：`satisfaction_window < advisory_min_ratings` → `ValueError`（pydantic 会包成 ValidationError，符合"配置错误大声失败"）。
5. 脱敏默认新增两条 pattern（`RedactionCfg` 默认列表 + `config/glimpse.toml` 同步）：
   - email：`[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}`
   - 分隔符容忍的长数字：`(?<![\d-])\d(?:[ -]?\d){9,}(?![\d-])`（10 位及以上、允许单个空格/横线间隔）。

**测试契约：**
- `test_recall_error_result_logs_and_returns_empty`（caplog 断言 WARNING 出现 + 返回 []，用假的 searcher monkeypatch，**不 import 真 mempalace 模型**——参考 test_mempalace_memory.py 现有 mock 手法）。
- `test_match_sku_failure_is_logged`（matcher.match 抛异常 → caplog 有 exception 记录 + 返回值仍是友好文案）。
- `test_explicit_missing_config_path_raises` / 既有 `test_defaults_when_no_file` 保持通过（None 路径行为不变）。
- `test_feedback_window_smaller_than_min_ratings_rejected`。
- 脱敏双向：`test_default_patterns_redact_email_and_separated_numbers`（`a.b@test.com`、`6212 3456 7890 1234`、`621-234-567-890-1` 被遮；**反向断言**：`"一共99元，买了3件"`、`"电话 400-123"` 这类短数字**原样保留**——过度脱敏与漏脱敏同罪）。

**坑点：** ① 新 regex 先在 `./.venv/bin/python` REPL 里对着正反例跑一遍再进代码；② `(?<![\d-])` 的负向后顾是防止把 `400-123-4567` 的尾段单独再匹配一次造成嵌套遮蔽——保留它；③ evals `__main__` 没有测试覆盖，改完手动 `./.venv/bin/python -c "import evals.__main__"` 确认可导入（不执行）；④ pydantic 的 `model_validator` 在 strict-mypy 下需要正确的返回类型注解（`-> "FeedbackCfg"`）。

**验收标准：** 基线四命令全绿（brain ≥ 201）；`git grep -n "except Exception" brain/src/glimpse_brain/sku/tool.py` 的分支体内必须含 `log.exception`；删除任一新 validator/warning 后对应测试变红（自查）。

---

## Phase 4 — 记忆键正确性（brain）

**动机：** 记忆按 OCR 出的显示名做 wing key，`_safe_wing` 有损坍缩（`"王先生"`、`" 王先生."`、`"王/先生"` 可能同键），不同联系人会共享记忆——跨客户污染。

**架构决策（已定）：** wing = `"c-" + sha256(customer.encode("utf-8")).hexdigest()[:16]`。原始显示名进 metadata 新键 `customer_display`（供人工排查），**不参与检索过滤**。`_safe_wing` 删除。这是**破坏性变更**：现有 palace 里旧 wing 键下的记忆将检索不到——产品尚在开发期，Planner 已接受，不做迁移工具（YAGNI）；在 README 记忆章节加一行说明。

**模块边界：**
- 可改：`mempalace_memory.py`、`brain/tests/test_mempalace_memory.py`、README 记忆章节一行。
- 不可改：`Memory` Protocol、`memory_tools.py`、server 的记忆调用点。

**测试契约：**
- `test_wing_key_distinguishes_similar_display_names`：`"王先生"` 与 `" 王先生."` 产生**不同** wing（旧实现同键 → 先红）。
- `test_wing_key_is_stable`：同名两次调用同键。
- `test_write_metadata_carries_display_name`：upsert 收到的 metadata 含 `customer_display == 原始名`（mock collection 捕获参数）。
- 既有 integration 测试（`-m integration`，默认 deselected）不改动——它连真模型，不在验收命令内。

**坑点：** ① `hashlib.sha256` 记得 encode utf-8——中文名直接 hash 字符串会 TypeError；② metadata 值类型受 chroma 限制（str/int/float），别塞 None；③ 检索侧 `searcher.search_memories(wing=...)` 与写入侧必须用同一个函数生成 key——提取模块级 `def _wing(customer: str) -> str` 一处定义。

**验收标准：** 基线四命令全绿（brain ≥ 204）；`git grep -n "_safe_wing" brain/src` 零结果。

---

## Phase 5 — Shell 捕获与 UI 修整（shell + brain 一行）

**动机：** ① DiffGate 在未变帧上滚动更新基线，渐变内容永远触发不了 OCR；② `CaptureEngine.start()` 无并发防护，快速重选区域可泄漏活的 SCStream；③ RegionSelector 无取消路径，Esc 无路可走、误拖一像素会覆盖已存标定；④ `ContactReader.current` 跨线程读写无同步；⑤ `_on_summarize` 成功后 overlay 卡在 thinking。

**模块边界：**
- 可改：`DiffGate.swift`（仅 DiffGate 类，`Diff` 枚举不许动）、`CaptureEngine.swift`、`RegionSelector.swift`、`ContactReader.swift`、`main.swift`（仅选择器回调处）、`InputBoxCalibrator.swift`（若其复用 RegionSelector 则同步适配）、`server.py`（一行）、对应测试。
- 不可改：`Overlay.swift`、`Sender.swift`、点击捕获链路（上一批刚验收过）。

**执行要求：**
1. **DiffGate：** 基线只在**判定为变化时**更新：`isChanged` 改为——`previous == nil` → 存样本返回 true；`score > threshold` → 存样本返回 true；否则**不更新** previous 返回 false。这样亚阈值渐变会相对固定基线累积，最终触发。
2. **CaptureEngine：** 改为 `actor CaptureEngine`。`start`/`stop` 天然串行化（后写者等待前者完成）；`SCStreamOutput`/`SCStreamDelegate` 回调方法标 `nonisolated`，回调里只读 `onFrame`（改为 `private let` 化困难则用 `nonisolated(unsafe)` + 注释说明它只在 start 里写、frameQueue 里读，沿用仓库"confinement-by-comment"既有模式）。`main.swift` 调用处已是 `Task { await ... }`，签名兼容。**不许**给 stream 回调加锁——热路径。
3. **RegionSelector：** `init(onDone: @escaping (CGRect?) -> Void)`——`nil` = 取消。Esc（keyDown 53）或拖拽结果小于 10×10 pt → `onDone(nil)`。编译器会强制三个调用点（selectRegion / calibrateContactRegion / calibrateInputBox）适配：nil 分支只做 `self?.xxx = nil` 释放引用 + overlay 提示"已取消"，**不写 Store**。
4. **ContactReader：** `current` 改为由 `OSAllocatedUnfairLock<String>` 保护（`withLock` 读写），删除"bounded-staleness race"的辩解注释，行为语义不变（3s 轮询 + readFresh 仍在）。
5. **brain 一行：** `_on_summarize` 成功发出 `SummaryMsg` 后补发 `StatusMsg(state="watching")`。

**测试契约：**
- `diffGateHoldsBaselineOnUnchangedFrames`（DiffGateTests 已存在，扩展）：构造样本序列 A、A+ε、A+2ε…（每步 ε 低于阈值但累计超过）→ 旧实现永远 false，新实现最终 true（先红）。
- `regionSelectorTinyDragIsCancel`：RegionSelector 的最小尺寸判定若可提为纯静态函数 `static func isValidSelection(_ rect: CGRect) -> Bool` 则测它（10×10 边界两侧）；窗口/事件部分不做单测（能力边界⑤）。
- brain：`test_summary_returns_status_to_watching`——summarize 流程末尾能 `read_until(reader, "status")` 且 `state == "watching"`（注意先读走 thinking 那条：`read_until` 会停在第一条 status——**先收 thinking，再收 watching**，两次调用）。
- CaptureEngine 无法单测（SCK 权限，能力边界②）——验收靠 `swift build` + actor 隔离由编译器保证，PR 描述里写明。

**坑点：** ① actor 化 CaptureEngine 后 `NSObject` 继承与 actor 冲突——SCStreamDelegate 要求 NSObjectProtocol：**保留 class + 内部用 `AsyncSemaphore`/串行化任务**是备选方案；若 actor 路线 30 分钟内编不过就切备选：`private var startTask: Task<Void, Error>?`，`start` 先 `await startTask?.value` 再自替换（把决策记进提交信息）。② RegionSelector 回调签名变更是**源级破坏**——编译器会把三个调用点全找出来，别手动 grep。③ `OSAllocatedUnfairLock` 需要 `import os`；Package 平台 v14 ✓。④ brain 那行别忘了 `_on_summarize` 的 CostCap/异常分支**不该**发 watching（它们已发 degraded）——只在成功路径发。

**验收标准：** 基线四命令全绿（brain ≥ 205，shell ≥ 63）；`swift build` 无新警告（`swift build 2>&1 | grep -c warning` 不高于 main 分支同命令值）；手工冒烟（可选但推荐）：`scripts/dev.sh` 起进程，选区→Esc 取消→原标定仍在。

---

## Phase 6 — 测试补强、类型清零、依赖与文档（brain + repo）

**动机：** ① 入库前脱敏无测试守护（删掉 `redact()` 调用 195 个测试照样全绿）；② malformed IPC 行的存活路径无测试；③ `test_satisfaction.py:41` 断言私有属性 `_advised`；④ 死代码 `suggester.py` + 其 105 行测试虚增覆盖；⑤ pyproject 缺 numpy/onnxruntime 直接依赖声明；⑥ mypy 10 个既有错误让 README 的验证命令是假的；⑦ README macOS 版本不实、OKF spec 说 dev.sh 会播种 knowledge 而它没有。

**模块边界：** 可改：`brain/tests/*`、`brain/pyproject.toml`、`brain/src`（仅类型注解级修改）、`README.md`、`scripts/dev.sh`；**删除**：`brain/src/glimpse_brain/suggester.py`、`brain/tests/test_suggester.py`。不可改：任何运行时行为（本阶段是零行为变更阶段——mypy 修复若迫使行为改动，停，交回 Planner）。

**执行要求：**
1. 新测试 `test_memory_capture_is_redacted`（test_server.py）：开 memory（传 FakeMemory 进 `GlimpseServer(..., memory=...)`，capture `write` 参数），OCR 一条含 `13812345678` 的带 contact 消息 → `write` 收到的文本不含原号码。
2. 新测试 `test_malformed_line_keeps_connection_alive`：发一行 `not json\n` → 再发合法 OCR → 正常收 ack/建议；且 events 里有 `bad-message`（读 tmp event log 文件断言）。
3. 重写 `test_drop_then_rise_refires`（test_satisfaction.py）：不碰 `_advised`，改为行为断言——高分→建议触发（record 返回 True 一次）→ 跌破阈值 → 再回升 → **再次**返回 True。删除对私有属性的一切引用。
4. 删除 `suggester.py` 与 `test_suggester.py`；`git grep -n "suggester" brain/` 除 agent.py docstring 里"Replaces Suggester"историческая一句外零引用（那句保留）。
5. `pyproject.toml` dependencies 增加 `"numpy>=1.26"`, `"onnxruntime>=1.17"`（它们已被 sku/ 直接 import，只是此前靠传递依赖侥幸）；dev 增加 `"types-PyYAML"`。
6. mypy 清零，逐个（全部是注解级，无行为变更）：
   - `okf.py:11` → dev 依赖 types-PyYAML 解决；`okf.py:31` → `tuple[dict[str, object], str]`；
   - `distill.py:47,49`、`judge.py:32`、`server.py:121` → 补 dict 类型参数；
   - `server.py:180` → `-> None`；
   - `agent.py:116` → `output: str | ToolImage` 显式声明；
   - `tooluse.py:156` → `cast(Iterable[MessageParam], messages)` 或构造时就用 `MessageParam`（Cursor 二选一，禁用 `# type: ignore`）。
   - `sku/embedder.py:12` → `[[tool.mypy.overrides]] module = "onnxruntime.*" ignore_missing_imports = true`（第三方无 stub，白名单是正解）。
7. README：`macOS 13+` → `macOS 14+`；测试命令段确认 `mypy` 现在真实通过；记忆章节加一行 wing-key 变更说明（Phase 4 的破坏性变更）。
8. `scripts/dev.sh`：在播种 config 的同一段落，若 `~/.glimpse/knowledge/` 不存在则 `cp -R playbook/knowledge "$HOME/.glimpse/knowledge"`，若 `~/.glimpse/playbook.md` 不存在则 `cp playbook/playbook.md "$HOME/.glimpse/playbook.md"`（对齐 OKF spec §dev.sh 与 README 的 fallback 描述；同样**只播种不覆盖**）。

**坑点：** ① `test_memory_capture_is_redacted` 记得 OCR 消息要带 contact（无 contact 不写记忆）且文本要能过 tracker（inbound 模板即可）；② 删 suggester 前先 `git grep`——evals 不 import 它（已核实），但再查一次是你的责任；③ mypy 的 `MessageParam` 在 anthropic SDK 的 `anthropic.types` 下，import 路径写对；④ `types-PyYAML` 装进 venv：`./.venv/bin/pip install -e '.[dev]'` 重跑；⑤ README 的 mypy 数字别写死在文档里（"10 个已知错误"这类话删掉）。

**验收标准（本阶段最严）：**
```
cd brain && ./.venv/bin/python -m pytest -q          # ≥ 207 passed（含新增3、删除 suggester 的 -N）
cd brain && ./.venv/bin/python -m ruff check src tests  # All checks passed
cd brain && ./.venv/bin/python -m mypy               # Success: no issues found ← 本阶段核心指标
cd shell && swift test && swift build                 # 61+ passed, Build complete
bash -n scripts/dev.sh                                # 语法通过
```
外加自查：把 `server.py` 里 memory 写入前的 `self._redactor.redact(line)` 临时改成 `line` → `test_memory_capture_is_redacted` 必须变红（这是本阶段存在的意义），改回。

---

## 明确不修清单（Planner 决策，写这里防止 Cursor "顺手修"）

| 项 | 理由 |
|---|---|
| RateLimiter 按"轮"而非按 API 调用计费 | spec §phase4 明确设计如此（max_iterations 封顶轮内调用）；对抗审查 PLAUSIBLE 未定罪。**只许**在 `llm.py` 的 RateLimiter docstring 加一句说明，不许改行为。 |
| IPCClient `disconnect()` 的 fd 生命周期 | 审查验证：串行队列 FIFO 保证 teardown 先于重连，结构上不可达。 |
| OCR 显示名本身的不稳定（同一客户名 OCR 抖动） | 输入端固有噪声，Phase 4 的稳定 hash 已把"键的坍缩"消掉，名字抖动属产品级问题（需要用户校准联系人区域），越权。 |
| `socket_path` shell 侧硬编码 | 产品契约：shell 永远连 `~/.glimpse/glimpse.sock`。仅在 `config/glimpse.toml` 该行上方加注释说明（Phase 6 顺手完成，一行注释不算修）。 |
| fsync-per-append 的性能 | 崩溃尾迹 > 吞吐，设计意图明确（代码内注释在案）。 |

## 阶段依赖与顺序

```
Phase 1 (events) ──┐
Phase 2 (IPC)    ──┼── 相互独立，可并行分支，但 merge 按 1→2→3→4→5→6 顺序（冲突面：server.py 在 1/2/5/6 都有小改动，顺序 merge 免 rebase 地狱）
Phase 3 (静默失败) ─┤
Phase 4 (记忆键)  ──┘
Phase 5 (shell)   —— 独立
Phase 6 (清尾)    —— 必须最后（依赖前面全部 merge 后统一清 mypy/README/测试数字）
```

每阶段预计一个 Cursor 工作会话内完成。任何阶段卡死 > 2 次往返，停下来把现场（分支名、红的测试输出、diff）交回 Planner。
