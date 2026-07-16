# 选品数据飞轮对齐审计（First-Principles Audit）

> 审计基准：**客户目标**——记录全部浏览页面/点击习惯/停留时长 → 沉淀选品轨迹 + 最终选品结果 → 与商品卡/带货视频的流量/CTR/GMV 数据 join → 数据飞轮，让选品可量化。
> 方法：4 个独立透镜（采集完整性 / 数据基底 / 资产复用 / 第一性采集原语）并行深读代码，全部发现有 file:line 锚定；Planner 综合。
> 日期：2026-07-16 · 代码基线：main（backlog remediation 六阶段完成后）

---

## 一、总判定

**代码库与客户目标存在系统性错位，但地基可用。** 飞轮的四个必备器官——页面/商品身份（JOIN key）、停留时长、会话/轨迹边界、选品结果标注——**今天一个都不存在**（结构性缺失，不是没接线）。数据基底还在两处主动销毁飞轮价值：50 MiB 单代轮转**丢弃历史**，默认脱敏把 10+ 位数字**连商品 ID 一起遮掉**（JOIN key 在写盘瞬间被永久销毁）。与此同时，代码库最坚硬的四分之一（采集管线、IPC/spool、事件底座、fail-closed 白名单）恰好是飞轮需要的底盘——**换器官，不换底盘**。

一句话：今天每跑一天，产生的都是飞轮永远用不上的数据。先止血（基底），再补器官（采集），然后转第一圈（轨迹+标注），最后接外部数据（join）。

## 二、第一性拆解：飞轮需要的信号链

```
页面/商品身份(key) → 注意力(dwell/scroll) → 轨迹(session) → 结果(outcome 标注) → 外部指标(CTR/GMV) join
     ↑今天:无            ↑今天:无               ↑今天:无          ↑今天:无              ↑今天:无 key 可 join
```

每一环依赖前一环。当前唯一的行为信号是：左键点击瞬间 + app bundleId + 坐标 + 600×400 裁剪的 OCR 文本（`ClickSensor.swift:107,176-179`、`protocol.py:48-55`）——一个为客服产品设计的"点击触发的 OCR 采样器"，不是浏览行为记录器。

## 三、缺口矩阵（四 blocker + 基底三伤）

| # | 缺口 | 现状（锚点） | 为什么致命 | 修法方向 | 量级 |
|---|---|---|---|---|---|
| B1 | **JOIN key**：无 URL/页面标题/商品 ID | ClickMsg 无任何页面身份字段；窗口标题在已获取的 window-info dict 里但被丢弃（`ClickSensor.swift:66-74`） | 行为侧没有 key，goal 3 的 join 数学上不可能 | 窗口标题（现成）+ AX 读浏览器 tab URL + 协议加字段 | M |
| B2 | **停留时长**：事件 tap 只监听 leftMouseDown | 无 scroll/mousemove/app 激活/焦点/空闲检测，全仓 grep 零命中 | dwell 是选品注意力的头号隐式信号；点击间隔推不出 | AttentionSensor：NSWorkspace 激活 + AX 焦点 + 空闲门控 → DwellMsg | M |
| B3 | **会话/轨迹**：点击是无 session 的平铺流 | ClickMsg 连 seq 都没有（对比 OcrMsg，`protocol.py:24-33` vs `48-55`） | 飞轮的原子记录是轨迹不是孤立点击 | sessions.py（空闲间隙+连续性，纯代码）+ 开始/结束选品菜单 | M |
| B4 | **结果标注**：无 selection_outcome 通道 | 唯一标注通道绑死 CS suggestion（`protocol.py:66-72`） | 无标注 = 有特征无目标变量，什么都学不出 | SelectionOutcomeMsg{session, product_key, selected/rejected/shortlisted, note} + 菜单/热键 | **S**（杠杆最高） |
| S1 | **轮转丢历史** | `os.replace` 单代覆盖，总量硬顶 ~100 MiB（`events.py:39-42`） | 飞轮=纵向积累；GMV 数据晚到数月，历史早没了 | 归档式轮转（日期分代、gzip、永不删）；默认 keep forever | S |
| S2 | **脱敏销毁 JOIN key** | 10+ 位数字全遮且不可逆（`config.py:65-72`），淘宝/1688 商品 ID 正是 10-19 位数字 | 已采数据永久失去实体身份 | 脱敏按事件类别分道：CS 对话全遮不变；habit 事件保留数字 ID / 类型化占位符（⟨NUM:12⟩） | S |
| S3 | **无 schema、不可查询** | payload 自由 dict、无版本号、全文件线性扫描（`events.py:44`、`summarizer.py:91`） | 队列/漏斗/join 查询需要谓词下推 | SQLite（stdlib，零新依赖、零硬件成本）为分析主存，JSONL 保留为 ingest 日志 | M |

另有 major 级：burst 点击静默丢弃 + OCR 失败连点击事实都不记（`ClickSensor.swift:159`）——损失偏向高强度比价时刻，系统性偏置；Block 只存 x 不存 y（点了价格还是评论区分不出来）；Summarizer 输出一段散文不落库（把直觉重新编码成另一种不可量化的直觉）。

## 四、第一性判定：采集原语要换档

**核心赌注审判**：选品压倒性发生在浏览器（1688/淘宝/抖店 web），而默认白名单恰恰就是 Chrome+Safari——用最贵、最模糊的原语（逐点击截屏+OCR，每次 100-300ms Vision 推理）去观察一个**本可以免费拿到精确数据**的表面（MV3 扩展：URL→offerId 精确 JOIN key、Page Visibility→真实 dwell、webNavigation→全覆盖+天然会话边界，全部零 CPU）。

**判定：混合架构，扩展优先。**
- 浏览器层 → MV3 扩展 + native messaging host 喂给现有 brain（同一 JSONL 事件底座）
- 原生 app 层（千牛/抖店桌面端/微信）→ 现有 OCR 点击链**降级为差异化 fallback**——这是扩展够不着、也是这套 macOS 管线独有的价值
- **不要**再投本地 ONNX/视觉模型去给浏览器 OCR 降噪——那份预算属于扩展（直接命中你的按设备定价/硬件门槛顾虑）
- ToS 姿态：扩展只被动读取不自动化操作，姿态上比 DOM 抓取机器人干净

**中间态**（扩展未建成前）：AX API 在点击/焦点变化时读浏览器 tab URL，先把 JOIN key 拿到手——精度不如扩展但当天可用。

## 五、资产处置表

| 处置 | 模块 | 说明 |
|---|---|---|
| **直接用（底盘）** | ClickSensor+fail-closed 白名单、窗口作用域截屏、IPC+OutboundSpool、EventLog（fsync/fail-loud）、redaction 机制本身、DiffGate/CaptureEngine | 全库工程质量最高的四分之一，恰好是需要的 |
| **改造后用** | Summarizer（散文→结构化 JSON 落库，prose 只是渲染层）；redaction（按事件类别分道）；evals harness（补选品轨迹金标）；SKU CLIP matcher（未来做原生 app 的像素→SKU 实体解析器，**暂缓**）；MemPalace（键控轴 customer→(entity_type, entity_id)，**暂缓**——goal 3 的 join 是关系型的，SQLite 先行；300MB embedder 与硬件约束冲突，默认关） |
| **隔离（第二产品线，不是删）** | 自动发送全栈（Sender/SendPlanner/SyntheticInput/倒计时/联系人校准）、客服 agent 提示词、feedback/satisfaction、按客户记忆 | 用 build target + config flag 隔出去。这是合法的 CS 产品线，但对这个客户是范围债 |

## 六、翻案记录（Planner 诚实记账）

两处"基底伤"是我在 CS 基准下亲手做的**当时正确**的决定，审计基准变更后需要翻案：
- P1 的 50 MiB 单代轮转：为"审计尾迹"设计（防磁盘爆炸），对 CS 对，对"积累"错。
- P3 的长数字全遮：为"客户 PII 不泄给 LLM"设计，对 CS 对，对"自我追踪+实体 join"错——隐私主体从"客户"变成了"操作者自己"，威胁模型整个变了。

这不是缺陷返工，是需求基准位移。设计假设记录在案，防止未来再无声漂移。

## 七、建议路线图（P7.x，接续既有编号）

| 阶段 | 内容 | 量级 | 为什么这个顺序 |
|---|---|---|---|
| **P7.1 基底止血** | 归档轮转（永不删）+ SQLite 分析主存 + 事件 schema 类型化/版本化 + 脱敏按类分道 | S+M | 现在每天的数据飞轮都用不上——先让积累成立 |
| **P7.2 采集补全** | 窗口标题+AX URL（JOIN key）→ AttentionSensor（dwell）→ 点击完整性（失败也记、burst 排队）→ Block 补 y 坐标 | M | 有 key 有 dwell，信号链前两环通 |
| **P7.3 轨迹与标注** | sessionizer（纯代码）+ 开始/结束选品 + SelectionOutcomeMsg | S+M | 第一圈闭环：轨迹→结果。B4 是全场杠杆最高的 S |
| **P7.4 浏览器扩展层** | MV3 扩展 + native messaging host + BrowseMsg/DwellMsg；OCR 链定位为原生 app 层 | L | 精度质变；P7.2 的 AX 方案先顶住，不阻塞第一圈 |
| **P7.5 产品线隔离 + 量化输出** | CS 栈 build flag 隔离；Summarizer 结构化；首批选品轨迹 eval 金标 | M | 认知负担和二进制瘦身 |
| **P7.6 外部数据 join（第二圈）** | CTR/GMV 导入（CSV 起步）→ SQLite join → 量化报表 | M | 数据到位后飞轮真正转起来 |
| 暂缓 | MemPalace 换轴、SKU CLIP 实体解析 | — | 等语义召回/原生 app 解析有真实需求再立项 |

**验收哲学不变**：每阶段独立分支 + 四命令门 + 变异自查 + Planner 独立 gate（沿用 backlog remediation 的流程，全程有效）。

## 八、风险与边界

- **隐私**：自我追踪 ≠ 无隐私——浏览记录含个人内容；habit 事件本地明文的前提是"数据不出本机"；一旦未来做云同步/多设备，脱敏分道策略要重审。
- **平台 ToS**：扩展保持被动读取；不做自动化下单/爬取。
- **硬件/定价**：本路线图新增依赖为零（SQLite=stdlib、AX=系统 API、扩展跑在浏览器里）；唯一的重资产（CLIP/embedding）全部处于"暂缓/默认关"。
- **actor 可重入遗留项**（P5 记录在案）：若 P7 引入程序化快速重采，需先补 CaptureEngine 的 startTask 链式串行。

---
*四透镜原始发现（每条含 file:line、gap、建议、量级）存档于 workflow `wf_267db7ce-c57` journal；本文为 Planner 综合。*
