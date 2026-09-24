# 《贾维斯类个人 Agent 设计方案 v0.1》审核意见

- 审核对象:`jarvis-agent-design-v0.1.md`(2026-08-29 方向稿)
- 审核方式:全文精读 + 对文中 20 余项生态事实做联网复核(memU 上游源码/ADR/README、OpenClaw 官方文档与 issue、GitHub API 实测 star 数与语言、ACL 论文原文、衍生项目仓库)
- 审核日期:2026-08-29

## 审核结论(先给结论)

**方向判断正确,调研广度真实(未发现虚构项目),但存在 3 处核心论断不成立、事实基座基于旧快照、以及若干工程级缺口。按现稿直接进入 M1 有风险;建议按 P0 修订后,先做 M0 技术预研(重点是跨语言集成验证),再评审放行。**

值得保留的三件事:①「不做底座,做记忆+主动+治理」的定位判断与生态现实相符;②「记忆>能力」的认知正确,跨时间线问答作为 M1 生死点是好设计;③打扰预算、一键关闭、可逆性判定、显式纠错——这四个都是产品真问题,方案抓住了。

---

## 一、做得对的(保留项)

1. **定位判断成立**。复核确认:记忆层、主动性(注意力调度/打扰预算)、治理层确实是生态空白或薄弱项(OpenClaw 记忆是扁平 Markdown;rho 只有主动 check-in;IronClaw 有沙箱/安全基线但无权限四级与可逆性判定)。
2. **寄生策略务实**,与「复用优先于自研」原则自洽;附录项目清单(ZeroClaw/NanoClaw/IronClaw/moltis/Clawlet/SubZeroClaw/rho/Gaia/ReMe/Memobase/Text2Mem)经逐一核查**全部真实存在**,不是幻觉,说明调研方法靠谱。
3. **M1 生死点设计**(「上周跟张总聊的方案后来改了什么」答得出来才算数)把抽象的「记忆层」折成了可证伪的验收场景,这是本稿最值钱的一笔。
4. **治理层把「可逆性」而非「权限清单」作为核心判据**,比绝大多数 agent 项目的安全设计高一个层次。
5. **风险表诚实**:memU 的 `dedupe_merge` 未实装、无 reranker、Python 版本要求等缺口都自己列出来了。

---

## 二、事实核查:需要更新的说法

> 关键发现:**设计稿整体对应的是 2026-06 前后的生态快照**,而 memU 与 OpenClaw 在这之后都有实质变化。文档脚注已声明「启动前复核」,但正文是直接把旧事实当既成事实做选型的,「复核」应升级为 M0 的硬性任务。

| # | 文档说法 | 复核结果 | 影响 |
|---|---|---|---|
| 1 | OpenClaw 本体 14.5 万 star | **过时**。GitHub API 实测 [openclaw/openclaw](https://github.com/openclaw/openclaw) ≈ **38.8 万星**(2026-03 已破 25 万),主语言 TypeScript(Node/Bun 运行) | 高:低估宿主生态体量与迭代速度,放大「寄生风险」 |
| 2 | OpenClaw 用 `MEMORY.md` 做「长期/日/会话」**三层**记忆 | 部分属实。官方[记忆文档](https://docs2.openclaw.ai/zh-CN/concepts/memory)为**两层**:日日志 `memory/YYYY-MM-DD.md` + 长期 `MEMORY.md`(可选);会话记忆仅实验性/可选。且官方文档明确 "This area is still evolving",已含自动记忆刷新、Dreaming 后台晋升、知识 wiki 层 | 高:对宿主的记忆能力现状**低估**,直接影响差异化论证 |
| 3 | memU 四实体 + 六分类 + 声明式工作流(`insert_after`)+ Python 3.13+/langchain-core | **仅旧版成立**。当前 [NevaMind-AI/memU](https://github.com/NevaMind-AI/memU) main 已被 ADR 0006/0007 重构:实体改为 Resource/RecallFile/RecallFileSegment;六分类(profile/event/knowledge/behavior/skill/tool)改为 track/line;Python ≥3.11 且移除 langchain-core;定位从「24/7 主动记忆 Agent」改为 "Personal memory across agents"。拦截器实际是 before/after/on_error + insert_before/after。设计稿等效于旧 commit(≈`357aefc8`) | **极高**:选型依据的「六分类法覆盖性强」「insert_after 挂载零成本」等理由要对着新版重新验证;要么定格旧版(fork/pin),要么按新版重构设计 |
| 4 | memU 13.8k star | 量级正确、轻微过时:实测 ≈14.4k | 低 |
| 5 | memU 无 `dedupe_merge`、无 cross-encoder reranker、存储 inmemory/sqlite/postgres、LLM profile 路由、Apache-2.0 | **属实**(新旧版均成立) | 无 |
| 6 | memU 多租户靠 `user_model`,无 row-level security | 部分属实:`user_id/agent_id` scope 隔离**属实**,「无 RLS」也属实(无 RLS policy,仅非唯一 scope 索引) | 低(单用户场景) |
| 7 | 「零适配成本」:memU 与 OpenClaw 天然契合可直接替换 | **不成立见问题 1**:Python ↔ TypeScript 跨语言集成,加上 memU 已改版 | 高 |
| 8 | IronClaw:WASM+Docker 沙箱、凭据注入、提示注入检测、端点白名单 | **属实**([nearai/ironclaw](https://github.com/nearai/ironclaw),Rust,security-first) | 无 |
| 9 | rho 是 always-on 主动 check-in 的参照 | **属实**([mikeyobrien/rho](https://github.com/mikeyobrien/rho),macOS/Linux/Android) | 无 |
| 10 | Graphiti DMR 94.8% / LongMemEval 63.8%;Mem0 LoCoMo 92.66% | 与各项目公开口径一致(均为**厂商自评**,非第三方评测;[Zep 论文](https://arxiv.org/abs/2501.13956)、[mem0 官方 benchmark](https://mem0.ai/blog/state-of-ai-agent-memory-2026));Cognee ~85% 同属自评 | 低:表格应注明「自评基准,跨方案不可直接比」 |
| 11 | Mem0「平铺,无类型、无时序」 | **部分过时**:Mem0 已于 2025-11/2026 引入 temporal reasoning,「平铺无时序」不再是它的全部 | 中:影响「不推荐 Mem0」的论据强度 |
| 12 | Text2Mem 作者 Lihai Yang | 论文真实(ACL 2026 Findings,[2026.findings-acl.100](https://aclanthology.org/2026.findings-acl.100)),但 Lihai Yang 是**第二作者**,第一作者 Leo Wang,共 9 人 | 低:勘误级 |
| 13 | 附录 ReMe「License:阿里 AgentScope 生态」 | ReMe 真实([AgentScope 文档](https://docs.agentscope.io/reme/latest/zh/overview),file-native 记忆);但「AgentScope 生态」不是许可证 | 低:表格列错 |
| 14 | 风险表「Graphiti 需 FalkorDB」 | 2.3 表自己写的后端是 FalkorDB / **Neo4j** | 低:风险表与选型表口径不一 |

**结论:调研没有编造,但「旧得快」是主要问题。** 尤其是 memU 改版一事,直接动摇选型章第二节的几条论据,必须在开工前解决。

---

## 三、核心问题(按严重度排序)

### 问题 1(策略级·高):「零适配成本」不成立——跨语言集成 + memU 已改版

- memU 是 Python 服务,OpenClaw 是 TypeScript/Node。替换其记忆层 = 常驻 Python 进程托管、Python↔Node IPC/HTTP、检索延迟预算、双份配置与故障降级——这是**第一个真正的工程成本点**,不是「零适配」。
- 更关键的是:方案里所有「为什么选 memU」的论据(六分类、四实体、declarative workflow 挂载点)都建立在**旧版**之上;当前 main 已改为 Resource/RecallFile/RecallFileSegment 与 track/line。**要么 fork/pin 旧版(≈`357aefc8`,与设计完全一致,但失去上游迭代与社区修复),要么按新版重新做适配与写入管线设计**——这是两个不同方向,现在是待决策却未列入「待决策」清单。
- **建议**:M0 增加一个 spike:「memU(新版或 pin 旧版)↔ OpenClaw 集成验证」,输出替换延迟、失败降级方案、是否需 fork、以及六分类在新版上的替代方案(或降级为自研数据模型、仅复用 memU 的文件化存储+LLM profile 路由)。「零适配成本」改为「适配成本待验证」。

### 问题 2(策略级·高):数据红线与云端认知层未闭环

- §7 承诺「原始个人数据不出端,上云只传必要摘要」;但 §1.2/§5 认知层用「云端强模型」,§4.1 写入管线要「按类型并发抽取」——**抽取的 LLM 跑在端侧还是云侧,全文没有说**。
- 若抽取上云:沟通/会议原文必然上云,与红线矛盾;若端侧小模型:抽取质量打折,而**记忆质量恰恰取决于抽取质量**,这可能是整个方案的核心权衡,没有回避的余地。
- 「必要摘要」无形式化定义 → 既无法验收,也无法向用户承诺「数据不出端」。
- **建议**:明确 ①抽取模型位置及端/云质量-成本权衡结论;②「必要摘要」的可操作定义(建议:仅含实体+关系+事件元组,不含原文复述,并做最小化/脱敏;或端侧本地推理 + 云端仅收下游任务摘要);③合规一节(PIPL、个人信息出境、IM 数据授权外的授权范围)。

### 问题 3(策略级·高):宿主正在向你的差异化方向进化,方案缺抽象边界

- OpenClaw 38.8 万星、记忆区官方自述 "still evolving",且有[公开 issue #67970](https://github.com/openclaw/openclaw/issues/67970)(要求内置记忆支持自动上下文抽取+日日志写入)——**这正是本方案 M1–M2 要做的核心能力**。文档记忆层已经在快速补课(自动刷新、Dreaming 晋升、wiki 层)。
- 寄生策略的隐含风险:宿主能力反超 → 差异化被稀释;宿主高频迭代 → 持续适配成本。方案只评估了「适配成本」一次,没有设计「反寄生」结构。
- **建议**:补一层「接口抽象」——定义自己的 MemoryPort / ProactivePort / GovernancePort(接口 + 最少行为契约),宿主作为可插拔实现(OpenClaw / ZeroClaw / moltis / 未来自研可换)。这是把「寄生」变成「依附可控」的关键设计,也是现方案唯一没有抽象边界的地方。

### 问题 4(策略级·中):「只做三层」与自研范围自相矛盾,「减少 60%」无口径

- §3 说自研三层(记忆/主动/治理);§5 又给感知层(端侧小模型降噪)、认知层(分层规划)、人格层列了自研重点——**实际自研面是 6 块**。
- 「自研工作量减少约 60%」缺基准:相对哪个「全栈自研」基线?没有计算过程,无法复核。
- **建议**:统一表述为「自研 = 记忆 + 主动 + 治理(核心三件套)+ 薄胶水(降噪筛选、分层规划)」,并给出 60% 的推导或删掉该数字。人格层建议维持低优先级并写明「此阶段仅人设文件,不做引擎」,避免范围滚动。

### 问题 5(技术级·高):记忆写入管线缺「隐式纠错」与评测闭环

- 「显式纠错入口」好,但长期运行的大部分错误是**隐式**的(用户后续行为与已存记忆矛盾)。缺隐式冲突检测(行为否决旧记忆、证据链失效)的设计,「自信地记错」会持续放大——方案自己定义了这个问题,却没有给出第二层防线。
- 去重(阈值/时间窗/类型策略)、纠错(证据链:谁在何时纠正了什么)都只到概念层。
- **建议**:①M2 前建「错误记忆基准集」(50–100 条:事实错误、过时事实、重复条目、模糊条目),做回归;②纠错动作本身进事件流(可审计、可回滚);③设计隐式冲突检测:写入时对 `profile/behavior` 类记忆做与既有记忆的矛盾检查,命中则降级为「待确认」而非直接覆盖。

### 问题 6(技术级·中):主动性引擎的宿主关系与机制细节空缺

- 事件源到底从哪来:文件变更(端侧 OS watch)、日历/邮件(OpenClaw 渠道 or 直连 CalDAV/IMAP)、阈值触发——主动性引擎是**宿主内 skill/cron**,还是**独立 sidecar 进程**?架构图「纵向贯穿」没有回答这个问题,而它决定 M3 的工作量与架构形态。
- 注意力调度:打分特征(时效性、历史采纳率、行动必要性、可延迟性)与反馈闭环(被忽略 → 降权)未定义。建议 v1 用规则 + 轻量评分,不要一上来就上模型打分。
- 「一键关闭该类提醒」需要提醒类别体系,建议与记忆六分类对齐或独立轻量分类,并在 M0 就定义(影响数据模型)。

### 问题 7(技术级·中):治理层「可逆性判定」缺实现载体;安全基线是清单不是机制

- 可逆性判定需要一个 `reversibility registry` 落到数据模型,生态没有,需自研。建议草案结构(示例):

```json
{
  "op_id": "op_20260829_001",
  "origin": "proactive/brief",
  "tool": "file.write",
  "params_hash": "sha256:...",
  "reversibility": "reversible | partial | irreversible",
  "undo_payload": "backup_path或具体逆操作",
  "revocation": "auth_level: L2 需确认",
  "ttl": "7d"
}
```

  并在§3 架构图中标注为治理层的子模块。缺少它,「可逆性」就只是原则,落不了地。
- 「直接借鉴 IronClaw」的安全基线是**清单**(沙箱/凭据/注入检测/白名单),不是机制方案。特提醒:**记忆是被污染的持久层**——本次某次对话里的恶意内容被抽取成 memory 后,每次召回都是注入载体,且经图谱多 hop 传播。需要:①记忆项来源标记(原始数据 hash + 可信度);②记忆内容进 prompt 前隔离渲染(不可信文本不得进入指令区);③对注入检测定义失败处置(拒绝召回 vs 清洗后召回)。
- OpenClaw 自带 secrets 机制,「凭据注入」需先核对与宿主方案的兼容性再设计,避免两层凭据管理打架。

### 问题 8(项目级):缺工作量、成本、指标、止损线

- **时间/人力**:里程碑只有能力标志,无周期估算,无关键路径与并行度。至少补 M0–M1 的周数与团队规模假设(单人还是 2-3 人,结论可能完全相反)。
- **成本模型**:LLM profile 路由「成本差一个数量级」要有预算支撑——晨间简报、沟通自动落库是高频路径,给出端侧 embedding/抽取模型 + 云端强模型的每日 token 预算估算。
- **量化指标**:除 M1 的一句「答得出来」,没有可测指标。建议:①跨时间线 QA 评测集 ≥50 条(含「后来改了什么」这类因果更新题);②记忆质量( top-k 召回命中率、错误记忆率、纠错率、重复率);③主动性(提醒采纳率、忽略率、15 日留存)。
- **止损线**:M0/M1 失败的标准与退出路径(回退纯 OpenClaw 的代价是什么)未定义。M1 生死点只说了「答不出来就停」,没说停到什么程度、资源预算多少。

---

## 四、勘误与格式问题(不改变方向,但该改)

1. §4.3 权限表「建议」示例「起草邮件但**发送**」——与规则相悖,应为「起草邮件(**不**发送)」。
2. §2.1 「本体 14.5 万 star」→ 约 38.8 万(2026-08 实测)。
3. §2.2 「OpenClaw 三层记忆」→ 两层(日日志 + 长期 MEMORY.md)+ 实验性会话索引;且官方文档已说明记忆区仍在演进。
4. §2.3 基准列:标注为厂商自评;Memo0 需补「已引入 temporal reasoning」;ReMe 的 License 列应为许可证(而非「阿里 AgentScope 生态」);Text2Mem 未进对比表但正文在讨论(结构小瑕疵)。
5. §3 复用层:人格层未归入「自研/复用」任何一侧(§5 才说低优先级),建议在架构图或正文明确归属。
6. §5 认知层：「直接用最强通用模型」(§1.1)与「自研重点是分层规划」(§5)并不矛盾,但表述建议合并,避免被读成两头下注。
7. §7 风险表「Graphiti 需 FalkorDB」与 §2.3「FalkorDB / Neo4j」口径不一。
8. 附录「OpenClaw — Node.js」→ 主语言 TypeScript(Node/Bun 运行)。
9. M0「导入历史数据」未定义范围(哪些数据、多少量、隐私处理),建议给出首期导入清单与上限。

---

## 五、建议修改优先级

**P0(放行 M0 前必须解决)**
1. 对 memU 当前 main 做一次正式复核(新实体模型/定位/依赖),决定:pin 旧版(≈`357aefc8`)还是按新版重构设计——新增为待决策项 #4。
2. 修正「零适配成本」表述,M0 增加跨语言集成 spike(见问题 1)。
3. 补接口抽象层(MemoryPort/ProactivePort/GovernancePort),把寄生风险显式管理(见问题 3)。
4. 数据红线闭环:明确抽取模型端/云位置 + 「必要摘要」的形式化定义(见问题 2)。
5. 统一自研口径,删除或推导「60%」(见问题 4)。

**P1(M0–M1 期间)**
6. 建 M1 跨时间线 QA 评测集(≥50 条)+ 记忆质量指标(见问题 5、8)。
7. 写入管线补「降噪」环节与隐式纠错设计(见问题 5)。
8. 治理层补 `reversibility registry` 数据结构草案(见问题 7)。
9. 主动性引擎:明确宿主关系(宿主内 vs sidecar)+ 事件源清单 + v1 打分特征(见问题 6)。

**P2(与 P0/P1 并行,改稿即可)**
10. 第四节全部勘误;里程碑补周期估算与止损线。

---

## 六、下一步建议

1. **先按 P0 修订一版(v0.2)**,重点解决「选型时效性」与「跨语言集成」两个不确定性。
2. **M0 技术预研先行**(建议 2–4 周):①memU 新旧版取舍与 OpenClaw 集成 spike;②跨时间线 QA 数据构造(50 条)与已有记忆方案(OpenClaw 原生产物 + 手动导入)的基线对比——这个对比同时回答「宿主内置记忆是否已够用」,直接验证差异化是否成立。
3. M0 完成后**再评审一次**再放行 M1;若集成 spike 证明替换成本超过预估(如 >1 人月),则回头讨论「自研轻量记忆层(参考 Clawlet 的 SQLite+sqlite-vec 路线)」作为 Plan B——方案附录里已有这个种子,值得在待决策清单里明确。

---

## 七、复核来源(节选)

- OpenClaw 仓库与记忆文档:[openclaw/openclaw](https://github.com/openclaw/openclaw)、[记忆文档](https://docs2.openclaw.ai/zh-CN/concepts/memory)、[memory.md](https://github.com/openclaw/openclaw/blob/main/docs/concepts/memory.md)、[#67970](https://github.com/openclaw/openclaw/issues/67970)
- memU:[NevaMind-AI/memU](https://github.com/NevaMind-AI/memU)、[DeepWiki 术语表](https://deepwiki.com/NevaMind-AI/memU/11-glossary)、[memU 说明书](https://doramagic.ai/zh/projects/memu/manual/)
- 衍生项目:[nearai/ironclaw](https://github.com/nearai/ironclaw)、[mikeyobrien/rho](https://github.com/mikeyobrien/rho)、[Virtual0ps/gaia](https://github.com/Virtual0ps/gaia)、[mosaxiv/clawlet](https://github.com/mosaxiv/clawlet)、[moltis-org](https://github.com/moltis-org)、[awesome-claws 清单](https://github.com/machinae/awesome-claws)
- 记忆协议/生态:[Text2Mem(ACL 2026)](https://aclanthology.org/2026.findings-acl.100)、[ReMe(AgentScope)](https://docs.agentscope.io/reme/latest/zh/overview)、[Graphiti/Zep 论文](https://arxiv.org/abs/2501.13956)、[Mem0](https://pypi.org/project/mem0ai/)、[mem0 记忆基准报告](https://mem0.ai/blog/state-of-ai-agent-memory-2026)

> 说明:star 数与版本状态以 2026-08-29 实测为准,属时效性数据,评审通过后需在启动日再验一次。
