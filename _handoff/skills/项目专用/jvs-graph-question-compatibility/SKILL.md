---
name: jvs-graph-question-compatibility
description: 核验 JVS/M1 的题集能否在其声称的图谱上评测，并估算构图成本。当出现「在某个图谱上评测某题集」「留出集/基线跑不了」「构图要多少调用」「候选对规模」「题面在图谱里检索不到」「D10/D09 评测」等场景时使用。
agent_created: true
---

# JVS 图谱 × 题集 兼容性核验与构图成本估算


## 为什么需要这个技能

JVS/M1 项目里，**题集与图谱是两个可以互相脱节的产物**。踩过的真实事故（2026-09-16）：

- D10 留出题集在 09-08 设计时取自 **R 编号**语料（393 事件，就在 v3 图谱里）；
  09-16 因独立性核查改用**重新抽取的 G 编号**语料（3,732 事件）重出题 —— 题集独立性达标了，
  但这批语料**从未构图**。
- 而 `holdout/overview.md` 仍写「在冻结的 v3 图谱上跑这 10 题」。
- 结果：「授权 20 次调用」的评测**根本无法执行**，且为它构图需 **1,661 批起**（授权的 83 倍）。

**结论：任何「在某图谱上评测某题集」的任务，动手前必须先做本技能的三步核验。**

---


## 三步核验流程

### 第 1 步：图谱里有没有这批事件？（编号空间核对）

题集事件的编号前缀必须与目标图谱的编号空间一致。

```bash
PY="C:/Users/<user>/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe"
"$PY" -B -u -c "
import sqlite3,glob
for p in sorted(glob.glob('**/causal_graph*.db',recursive=True)):
    try:
        c=sqlite3.connect(p)
        n=c.execute('select count(*) from events').fetchone()[0]
        mn,mx=c.execute('select min(id),max(id) from events').fetchone()
        g=c.execute(\"select count(*) from events where id like 'G%'\").fetchone()[0]
        print('%-40s events=%-6s id %s..%s  G=%d'%(p,n,mn,mx,g))
    except Exception as e: pass
"
```

- **`causal_graph_final_v3.db` = 基线 21/30 所用**：393 事件 / 295 边，id 空间 `R60001..R90019`。
- 若 `G=0` 而题集是 G 编号 → **该图谱无法评测该题集，立即停止。**

### 第 2 步：题面关键词能否在图谱里命中？（检索验证）

```bash
"$PY" -B -u -c "
import sqlite3
c=sqlite3.connect('01-host-product/2026-08-28-20-59-40/docs/host_memory_dump/causal_graph_final_v3.db')
for kw in ['火山','带宽','自动关机','最大帧间隔']:
    n=c.execute(\"select count(*) from events where subject||' '||coalesce(action,'')||' '||coalesce(object,'')||' '||coalesce(before,'')||' '||coalesce(after,'') like ?\",('%'+kw+'%',)).fetchone()[0]
    print(kw,n)
"
```

命中 0 即锚点不存在。**注意**：少量命中（2–3 条）可能是同组织其他会话的偶发词面重合，
不代表是该题的真实锚点 —— 需回到 payload 原文核对。

### 第 3 步：构图要多少调用？（官方管线实测）

**不要用「事件数 × 系数」拍脑袋。** 候选量随**同一时间窗内的事件密度平方增长**：
基线 393 事件（分散 2 批语料）→ 11,865 全量候选；D10 单群 G12（505 事件、日期集中）→ 61,169 对。

用官方管线跑（**纯离线，不产生模型调用**）：

```bash
cd "C:/Users/<user>/jvs-src/01-host-product/2026-08-28-20-59-40/tools/causal"
"$PY" -B -u s4_pipeline.py candidates \
  --events <events.jsonl> --out <candidates.jsonl> \
  --max-candidates 100000000 --window-days 7
```

- `--max-candidates` 开到全量才能看到真实规模；基线实际用 `600` 截断（→ 800 对 / 14 批）。
- **分批口径**：`BATCH_SIZE = 60` 对/批（`s4_pipeline.py:54`）→ 批数 = ceil(对数/60)。
- **性能警告**：`coref` 通道是 **O(n²)**。>1,500 事件会跑很久（实测 3,732 事件 3 分钟未完成）。
  **对策：按 `corpus`（群）逐群切分跑，再求和。**
- 事件 jsonl 需含 `corpus` 字段作为隔离域（D10 事件无 `corpus`，用 `group` 填充）。

**事件格式转换**（D10 `events-unreviewed.jsonl` → 管线格式）见
`02-m1-evaluation/outputs/m1-10-d10-holdout-eval-prep-20260916/scripts/build_d10_events.py`。

---


## 第 4 步：题集的独立性取证（**最容易被忽略的一步**）

「锚点在图谱里」≠「题能当留出集」。必须再查**材料级泄漏**：
候选事件是否已出现在开发材料中（事件清单、判边结果、定版说明、**模型原始答案输出**）。

```bash
"$PY" -B -u scripts/probe_path_c_materials.py   # 见交付目录
```

判据（实测教训，2026-09-16）：

| 层级 | 检查项 | 通过标准 |
|---|---|---|
| 事件级 | 与既有题集/gold 引用的编号重叠 | 应为 0 |
| 事件级 | 是否已是图谱边端点 | 最好为 0（是则说明判边时见过） |
| **材料级** | **事件在 `*.md`/`*.txt` 中出现次数** | **应为 0** |
| 提示级 | 是否出现在 `prompts/`、示例材料 | 应为 0 |
| 格式级 | gold 是否为 `cause/effect/evidence_hint` 三要素 | 必须结构化 |

**实测反例（路径 C 被否）**：09-08 草案的 10 题锚点 10/10 在图谱内、与题集重叠 0，
看似理想；但事件在开发材料中出现 **14–31 次**，命中文件包含
`m1_events*.md`、`flash_edge_*.txt`（判边结果）、`M1-正式图库定版说明-20260902.md`、
**`doubao_gui_ans_raw.txt`（模型原始答案输出）** → 硬否决。

> 判据：**只要事件编号在同项目的开发文档里出现过，该题就不宜作留出集** ——
> 它测的是「模型对已见材料的记忆」，不是泛化能力。

---


## 附页索引（**正文在 `references/`，按需打开**）

> 📄 本技能 2026-09-20 做过细分：`SKILL.md`（入口 + 流程 + 目录）+ **5 页附页**。
> **附页是按需读的** —— 别把 5 页一次性全读进来。

| 附页 | 何时读 |
|---|---|
| `references/陷阱-环境与通道.md` | 开跑前核对环境、换 provider / 换通道、排查「跑不起来」时 |
| `references/陷阱-检索与抽取.md` | 改检索 / 抽取层、评估抽取质量时 |
| `references/陷阱-判边与批处理.md` | 判边 / 批处理 / 排查「任务像死了」「结果不可信」时 |
| `references/评测与判分口径.md` | 判分、出结论、写「口径与成本报告」之前 |
| `references/基线与复现配方.md` | 复现 21/30 基线、报预算、排查配额与限流 |

**后两页含 10 节，不在上面的陷阱目录里** —— 需要时才开：

- `评测与判分口径.md`（6 节）：★补抽全链路验收指标 ｜ ★★★ `LLM_REV_EDGES=1`（反问句第二个开关）｜
  ★★★ 混合题型判分**必须分层**（否则虚假命中）｜ ★★★ `cause_hit` 合法分母是 **3**（推翻上一节）｜
  ★★★ 剔 weak 边**缩短不了可达范围** ｜ ★★★ 两套独立裁决怎么找真分歧
- `基线与复现配方.md`（4 节）：实测基线数据（含 3.1「截断不可救」限定、3.2 锚点邻域限定）｜
  ★配额与限流的排障（`deepseek-v4-pro` 是模型级且极紧）｜ 基线 21/30 的权威配方 ｜
  交付「口径与成本报告」的骨架

---

## 关键陷阱

> 📄 **本页只列目录（28 条）。正文已移入附页** —— 按箭头打开对应 `references/*.md`。
> 每条陷阱的完整正文（含实测数据、命令、判据）都在附页里，**不要凭目录标题就下结论**。

1. **题集与图谱的编号空间必须核对** → `references/陷阱-环境与通道.md`
2. **`targeted_round.py prepare` 写死 30 题** → `references/陷阱-环境与通道.md`
3. **`channel()` 硬约束** → `references/陷阱-环境与通道.md`
4. **生成端 `doubao2api` 在 PyPI 上不存在** → `references/陷阱-环境与通道.md`
5. **判分器分两套** → `references/陷阱-环境与通道.md`
6. **口径不一致就不能比** → `references/陷阱-环境与通道.md`
7. **gold 必须是三要素结构** → `references/陷阱-环境与通道.md`
8. **基线生成通道是 `doubao_sub` 本地桥，不是 ark** → `references/陷阱-环境与通道.md`
9. **写 JSON 产物一律「先写 `.tmp` + 校验 + `os.replace`」** → `references/陷阱-环境与通道.md`
10. **`doubao2api` 在本机是「已装」的 —— 别把搜索根写错** → `references/陷阱-环境与通道.md`
11. **检索污染：把非题群语料一起录入图谱会毁掉锚点召回** → `references/陷阱-检索与抽取.md`
12. **改检索层前必须确认真实调用路径** → `references/陷阱-检索与抽取.md`
13. **n-gram/关键词抽取要检查数字碎片** → `references/陷阱-检索与抽取.md`
14. **判边批产物的「完成度」会骗人 —— 必须做质量稽核** → `references/陷阱-检索与抽取.md`
15. **判边质量不能用启发式自动裁决，也不要拿另一个模型当"更高召回"的证据** → `references/陷阱-判边与批处理.md`
16. **`reasoning_effort` 会被官方封装静默吞掉 —— 这是判边截断的头号根因** → `references/陷阱-判边与批处理.md`
17. **档位阶梯与窗口预算：判得多少随思考强度单调上升** → `references/陷阱-判边与批处理.md`
18. **别把「与基座模型一致」当成判边质量基准** → `references/陷阱-判边与批处理.md`
19. **判断「数组是否完整」只能靠括号配对，绝不能看文件结尾形态** → `references/陷阱-判边与批处理.md`
20. **同一回包内模型会反复输出多次数组 —— 必须取「最后一个」** → `references/陷阱-判边与批处理.md`
21. **`temperature=1.0` 下不要用单批抽样推断档位规律** → `references/陷阱-判边与批处理.md`
22. **判断「批任务是否还活着」只能看产物增量，绝不能信 `tasklist`** → `references/陷阱-判边与批处理.md`
23. **`[]` 是人类可读的合法判定，不是空回包 —— 别把「小文件」当「故障」** → `references/陷阱-判边与批处理.md`
24. **判边质量要按「双模型交叉对照」评，单看完成率会选错通道** → `references/陷阱-判边与批处理.md`
25. **入库前必须校验 `type` 字段是否在合法枚举内** → `references/陷阱-判边与批处理.md`
26. **批量打同一网关时并发本身就是限流源 —— 必须全局节流 + 429 指数退避** → `references/陷阱-判边与批处理.md`
27. **★「丢因保果」：抽取阶段会丢掉根因消息、只留措施，而证据链看起来仍然完整** → `references/陷阱-检索与抽取.md`
28. **★ 抽检排序要用「客观判据」，别用 rationale 文本启发式** → `references/陷阱-检索与抽取.md`
