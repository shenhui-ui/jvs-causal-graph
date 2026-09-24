# 项目长期记忆

## 项目定位

对标「贾维斯」的个人 Agent 助手。两项已定前提：

- **落地场景**：个人工作助手（日程 / 任务 / 消息 / 文档）
- **部署形态**：混合式 —— 端侧常驻 + 云推理

## 核心战略（v0.2 定稿，2026-08-29）

**只做一件事：时序-因果图谱 + 跨会话因果问答。**

- **因果层** = 唯一自研核心
- **主动性** = 薄层（四开关 + 每日配额），复用宿主 heartbeat
- **治理** = 薄层（可逆性命名 + registry），复用宿主 exec 审批

宿主有**机制层**，缺**语义层/产品层**。薄层的正确定义 = 复用机制层，自研语义层。
自研即重复 = 会被宿主 1–2 个版本覆盖。

## 宿主能力核实（已查官方文档，勿再重复调研）

**heartbeat（主动性）实装**：`every`、`activeHours`（含时区）、`target`/`directPolicy`、
`showOk`/`showAlerts`/`useIndicator`（按渠道按账户）、防洪与冷却、scratch 检查清单（Agent 可自更新）、
`system event --mode now`、底层会话记录可审计重放。
**缺**：打扰预算（每日配额）、注意力调度、提醒类别体系。

**exec 审批（治理）实装**：`tools.exec.mode`（deny/allowlist/ask/auto/full）、
`security`/`ask`/`askFallback`、按 agent allowlist（glob + argPattern 精确 argv）、
文件绑定防漂移、`strictInlineEval` 拦截 `python -c`/`node -e`、审批 plan 存储复用、
`Exec finished`/`Exec denied` 事件、策略取更严格者、预设 yolo/cautious/deny-all。
**缺**：可逆性语义、权限四级制、操作级 registry。

**记忆 — Active Memory Plugin（v2026.4.10 起，重要）**：
在主 Agent 回复**之前**自动运行的记忆子 Agent，自动搜索并注入相关上下文。
另有 `memory-wiki`、`memory.search.rememberAcrossConversations`，配置见 `/reference/memory-config`。
→ **宿主已实现自动召回注入，因此绝不自研召回管线（粗排/中排/精排/reranker），那等于正面重复。**
→ 社区反馈：配置不当会「卡死」，寄生时注意。

## 易复发的事实陷阱（务必核对）

1. **memU 已按 ADR 0006/0007 改版**：四实体 → Resource/RecallFile/RecallFileSegment；
   六分类 → **track/line**（六分类已失效，不可再作为选型论据）；
   Py≥3.13+langchain-core → **Py≥3.11 且移除 langchain-core**；
   定位 24/7 主动记忆 → Personal memory across agents
2. **OpenClaw ≈38.8 万 star**（非 14.5 万），TypeScript/Node-Bun
3. **OpenClaw 记忆是两层**（日日志 + MEMORY.md），已含自动刷新、Dreaming 晋升、wiki 层，
   issue #67970 正要求内置自动上下文抽取 —— **宿主在补记忆层，故差异化必须上移到因果推理**
4. "零适配成本"不成立：memU 是 Python，宿主是 TypeScript
5. 基准数据（Graphiti 94.8% DMR / Mem0 92.66% LoCoMo / Cognee ~85%）均为**厂商自评**，不可直接横比
6. Mem0 已引入 temporal reasoning，"平铺无时序"不再成立

## 记忆层选型（v0.2）

**memU（新版，存储与路由基座）+ Graphiti（双时间轴时序骨架）+ 自研因果层**

- memU 版本取舍：**跟随新版**（依赖更干净，降低跨语言集成这一最大工程风险），不 pin 旧版
- Plan B：集成成本 >1 人月 → 自研轻量层（Clawlet 的 SQLite + sqlite-vec 路线）
- 否决：Mem0（分类弱）、全 Graphiti（架构重 + 图查询对 LLM 不友好）、Letta（需接受其 Agent 架构）

## 因果层铁律

1. **双层设计**：事实层（观测，硬保证，Graphiti 双时间轴）+ 因果层（推断，带置信度）
2. 每条因果边必须绑定 ≥1 个原始 Resource 作为证据；**无证据不写入**
3. 置信度不足 → **降级为时序陈述，绝不编造**
4. 用户可标注纠正因果边（既是纠错入口，也是优化信号）

## 因果层特有风险（务必防御）

**因果层 + 提示注入 = 风险放大器**。记忆是被污染的持久层，因果多跳展开会将污染沿因果边传播，
危害高于普通记忆检索。三层防御：
1. 来源标记（`source_hash` + `trust_level`）
2. 进 prompt 前隔离渲染，不可信文本不得进入指令区
3. **低可信来源的因果边不参与多跳展开**（因果层特有约束）
注入检测失败默认**拒绝召回**并告警，而非清洗后召回。

## 其他关键设计

- **接口抽象层（反寄生）**：MemoryPort / CausalPort / ProactivePort / GovernancePort，
  宿主作为可插拔实现（OpenClaw / ZeroClaw / moltis / 自研）
- **数据红线**：按敏感度分层路由（高敏感端侧抽、中敏感端侧初抽+云端精修、低敏感云端）；
  「必要摘要」= 仅结构化元组，不含原文复述，可抽检验收
- **纠错双防线**：显式（用户直接改，动作进事件流可审计）+ 隐式（写入时矛盾检查，命中降级为「待确认」而非覆盖）
- **画像层**：自研轻量分类（profile/behavior/skill/event/relation），不自研引擎。
  缺了它「比你还了解你自己」只剩一半
- **明确不自研**：召回管线（粗排/中排/精排/reranker）由宿主 Active Memory 提供。
  自研仅限「因果路径展开」这一宿主没有的环节

## 里程碑与验收

**M1 三级验收**（关键路径）：
1. 时序「方案后来改了什么？」—— 宿主原生能力可能已够用，无差异化
2. 因果「为什么改了？」—— **立身之本**
3. 反事实「如果当时没改，会怎样？」—— 纯检索系统做不到

**止损线**：M0 集成 >1 人月转 Plan B；M0 基线对比显示宿主已能答第二级则终止；
M1 因果准确率 <60% 回退弱方案。

## 用户当前约束

明确表示现阶段**不需要开发与代码工作**，只要方向与方案文档。

## 工作区文件

- `jarvis-agent-design-v0.2.md` — **当前执行版本**
- `jarvis-agent-design-v0.1.md` — 历史版本
- `jarvis-agent-design-v0.1-审核意见.md` — 严审意见（含 20+ 项生态事实复核）
- `jarvis-agent-design-v0.1-任务分解与派工方案.md` — 七波派工，约 38 人日（基于 v0.1，需按 v0.2 更新）
- `docs/` 目录
