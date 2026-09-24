# JVS 目录整理审计与方案

> 审计日期：2026-09-18 ｜ 方式：只读勘察（未移动 / 未删除任何文件）
> 结论：**布局不是"乱"，是被约束住的**；真正的混乱集中在 03 分区顶层与若干残留件。

---

## 一、先回答"要不要装个整理 skill"

已检索三个技能源（SkillHub 语义检索 / Vercel skills.sh / ClawHub），命中候选如下：

| 候选 skill | 安装量 | 它做什么 | 本项目适配度 |
|---|---:|---|---|
| `github/awesome-copilot@folder-structure-blueprint-generator` | 9.4K | 探测技术栈后**生成结构蓝图文档**，不搬文件 | 思路正确（只出蓝图），但面向 .NET/Java/React 等**代码项目**；本项目是"文档 + 数据产物"型，且已有更强索引层 |
| `wshobson/agents@python-project-structure` | 13.3K | 生成 Python **包**结构 | ✗ 本项目 03 分区顶层脚本是同目录互相 `import` 的**脚本集**，不是包（**当前数量见** `_index/功能分类总表.md` 附录，由生成器现算） |
| `composiohq/awesome-claude-skills@file-organizer` | 6.0K | 把文件**按类型搬到 Downloads/Desktop 式归档目录** | ✗ **对本项目有害**，会打断引用锚点 |
| `claude-office-skills/skills@file organizer` | 4.8K | 同上 | ✗ 同上 |
| `augmnt/webdev-skills@organizing-project-files` | 29 | Next.js `src/` 布局建议 | ✗ 完全不适用 |

### 结论：不装

三条理由：

1. **没有对口的。** 命中的要么面向代码工程（本项目不是），要么是"按文件类型搬家"的通用整理器。
2. **本项目的整理方向与通用整理器相反。** 通用整理器假设"移动文件 = 整理"；本项目 48,424 处硬编码绝对路径 + 24 个 junction + 2 个引用锚点，**移动即破坏**。
3. **已有的 `_index/` 索引层就是"专业整理"的正确形态**——它按 10 大类 × 51 子类对全库文件做了叠加标注，**零破坏**。这比任何现成 skill 都更贴合。

> 唯一值得借鉴的是 `folder-structure-blueprint-generator` 的**产出形态**：只生成蓝图/索引文档，不动物理布局。本项目已经在这么做。

### 补充评估：`duoduoler-ops/Table-skills` 的 `project-handoff`（2026-09-18 / 09-19）

同轮一并评估了「跨会话交接」类 skill，结论同样是**不整体安装**：

| 项 | 内容 |
|---|---|
| 仓库 | `duoduoler-ops/Table-skills`（MIT；含 `project-handoff` / `web-stand-in`） |
| 它做什么 | 在自动压缩检查点评估并提醒交接；保存交接材料；按授权新建接续对话 |
| 为什么不能整体装 | 「自动接续」依赖 `create_thread` / `list_projects` / `send_message_to_thread` 等工具 —— **WorkBuddy 宿主内建能力里仍无**（⚠️ 2026-09-19 更正：**本机 DSH 提供可编程等价底座**，见「重议条件」行）；提醒脚本硬编码 Codex 路径与 `CODEX_HOME` |
| 采纳了什么 | **仅方法层**（2026-09-19 移植；**逐文件状态见下方「二次更正」表，勿只看本行**）—— 记录 schema（决定状态三分类 + 进度状态四态 + 记录纪律）、4 条纪律（授权边界 / 提醒去重 / 展示规范 / 边界），以及**五分支入口 / 评估三条件 / 结果登记枚举 / 保存与恢复附页**。**全文落在技能 `jvs-project-handoff`**（含附页 `references/handoff.md`）；常用约定与登记表在 `HANDOFF.md` §11、§12 |
| 未采纳的部分 | **机制层** —— `prepare` / `evaluate --outcome` / `next_turn` / `respond defer` 等宿主 CLI 接口（**仍缺**），以及 `create_thread` 类跨会话寻址工具（⚠️ **2026-09-19 起部分有解**，见「重议条件」）。**能力缺口已如实登记**：门槛改为「阶段收尾自检 + §12 评估登记留痕」 |
| 重议条件 | ~~宿主提供等价的跨会话寻址工具后，再评估机制层~~ → **2026-09-19 改为「部分满足」**：GitHub 上无现成等价物（四类候选逐一否决），但**本机 DSH 的 `--profile acp` 提供 `session/new` / `session/prompt` / `session/list` / `session/resume`**，与缺失工具逐项对应（**零安装**、Apache-2.0）。⭐ **已端到端实测打通**（握手/创建/投递/模型回复/列举/落盘/关闭）；⭐ **成品桥已交付** = `tools/host/acp_bridge.py` + `README-acp-bridge.md`（零依赖，内建强制隐私闸门）。⚠️ **四点限制**：① 寻址的是 **DSH 会话，不是 WorkBuddy 会话**；② 上游自动接续第 3 步「接手方先只读核验」在 ACP 下**只能靠开场白软约束**（实测真的生效但**无强制力**）⇒ **不得宣称已 1:1 复刻**；③ **`session/fork` 实测 `-32601` 确证不可用**（分叉映射已下调）；④ ~~**ACP 会话内 git 不可执行**~~ → ✅ **2026-09-19 同日已定位根因并修复**：父进程未设 `PATHEXT` 时 **PS 5.1 自身 fallback 到 `.CPL`**（**与 PS 版本无关**，机器级注册表 PATHEXT 正常、用户级该值不存在）；桥已在启动 DSH 时注入正常值，实测 ACP 会话内 git 完全可用（报告 §9.6.4）。⚠️ **绝对路径不能绕过**（坏 PATHEXT 下静默失败、退出码 0）；⚠️ **手动起 DSH 不经桥仍会复现**。⚠️ **仍未完成**：与 `HANDOFF` §12 登记表未自动联动；触发条件（何时提醒）无解。勘察报告：`M1-10-跨会话寻址工具勘察-20260919.md`；方案 **D** 见 `_handoff/NEXT-交接提醒自动化.md` §5 |

> 与 R15 的区别：R15 是「**无对口 skill**」，本条是「**有对口但机制层不可用**」，
> 故按「**方法层全采纳 / 机制层不采纳**」处理，而非整条否决。
> 否决登记见 `HANDOFF.md` §10 **R16**。

#### ⚠️ 2026-09-19 二次更正：本表「采纳了什么」一栏曾**连错两次**

| 轮次 | 原写法 | 实际 |
|---|---|---|
| 第一次 | 「部分移植」 | 易被误读为「方法层也只移植了一部分」 |
| 第二次 | 「**方法层已全部移植**」 | ❌ **仍不实** —— 上游 `references/handoff.md`（**9.7 KB 纯方法层、零宿主依赖**）**从未移植** |

**实测移植状态（2026-09-19 逐文件核对上游仓库，非凭印象）**

| 上游文件 | 体积 | 状态 |
|---|---|---|
| `SKILL.md` | 5.9 KB | ✅ 已移植（技能正文） |
| `references/handoff.md` | 9.7 KB | ✅ **2026-09-19 补移植** → 技能 `references/handoff.md` |
| `references/compaction-reminder.md` | 5.7 KB | ❌ 机制层，未移植 |
| `scripts/compaction_reminder.py` | 23.7 KB / 473 行 | ❌ 机制层，未移植 |
| `hooks/codex-hooks.example.json` | 1.5 KB | ❌ Codex 专属，未移植 |
| `tests/test_project_handoff.py` | 24.8 KB / 508 行 | ❌ 机制层测试，未移植 |
| `agents/openai.yaml` | 0.4 KB | — 不适用（Codex 宿主元数据） |

> **教训**：这条断言**连错两次**，两次都是「**断言「状态」而非「事实」**」——
> 属「静默过期」家族。**唯一可靠的写法是逐文件核对清单，不是一句「已全部移植」。**
> 上游 README 自己也明写「**安装 Skill 不会自动提供宿主缺失的工具、接口或 Hook 权限**」。
> 机制层副本已留档 `C:\Users\<user>\_staging\upstream-table-skills\`，
> 供 `_handoff/NEXT-交接提醒自动化.md` 参考。

---

## 二、为什么"不能搬"

| 约束 | 数量 | 后果 |
|---|---:|---|
| 硬编码绝对路径 | **48,424 处** | 移动分区目录即大面积失效 |
| Windows Junction | **24 个** | 原路径可访问性依赖它 |
| 引用锚点 A：`01-.../docs/host_memory_dump/` | **194 个文件引用**（132 处以裸相对段出现，按 CWD 解析） | 改名即断 |
| 引用锚点 B：`03-.../2026-09-08-20-30-19/` | **1,200 个文件引用**（03 顶层脚本同目录互相 import；**当前数量见** `_index/功能分类总表.md` 附录，现算） | 移动即断模块解析 |
| SHA256 冻结指纹 | **13 项** | 行尾/字节变动即失效 |
| `_validation-stage-r*` 被引用 | **435 个文件**（台账 `acceptance-ledger.json` 直接指向 `-r16`） | 移动即断证据链 |

> ⚠️ **上表除「SHA256 冻结指纹 13 项」外，全部是 2026-09-19 的记录值，会随文件增删而变**
> （2026-09-23 补注）。它们的作用是说明**量级与后果**，**不要当现值引用**。
> **现测方法**：对每个锚点路径，统计「正文中出现该路径段」的文件数
> （用 ripgrep / Grep 工具按该路径段全文检索，计**命中文件数**，不是命中次数）。
> 「03 顶层脚本数」另有生成器现算版（`_index/功能分类总表.md` 附录标题）。
> 判据仍然是：**这些路径一旦移动就断链** —— 结论不依赖具体数字。

**判定：任何"把文件归到语义文件夹"的方案在本项目都是净负收益。** 整理只能以「索引标注 + 清残留 + 补治理」三种零破坏形态落地。

---

## 三、真实混乱点清单（已核实）

### 🔴 P1 — 内嵌孤儿 `.git`（治理隐患）

- 位置：`01-host-product/2026-08-28-20-59-40/.git/`
- 现状：**仍存在**，含 4 个提交，HEAD `c6c87e9`；主仓库未将其记为 gitlink（当前 0 个）
- 矛盾：`GIT-POLICY.md` 第八节明确写「**合并后移除内嵌 `.git`**」——**该步骤未执行**
- 风险：`git add -A` 一旦将其记成 gitlink，会**静默跳过整个 01 子树**
- 处置：确认 subtree 导入完整后移除（**不是** P1 里唯一需要 git 操作的一项）

### 🟠 P2 — 03 分区顶层：脚本 + 过程目录平铺（**数量见** `_index/功能分类总表.md` 附录，现算）

位置：`03-d10-workspace/2026-09-08-20-30-19/`

| 类别 | 数量 | 说明 |
|---|---:|---|
| `.py` 散文件 | 见附录 | `apply_*.py`、`build_*.py`、`verify_*.py` 全平铺（**数量与命名规律见** `_index/功能分类总表.md` 附录，现算） |
| `.json` 散文件 | 50 | 台账、探测结果、报告 |
| `.txt` / `.md` | 7 | 4 个 `.txt` + 3 个 `.md`（列表快照、报告等） |
| `*.bak-2026*` 残留 | **16** | 见 P3（其中 1 个以 `.py` 结尾） |
| `.log` | 1 | 已被 ignore |
| 过程目录 | **22** | `_validation-stage-r6…r19`（15）+ `_verify-*` / `_g05c09_*` / `_g08r4_*` / `_scratch-*` / `_caliber-scratch`（7） |
| 有意义的目录 | 4 | `outputs/`、`review-staging/`、`zero-revise-audit/`、`.workbuddy-ai/` |

**这是"感觉乱"的最大来源。** 但——

> ⚠️ **`_validation-stage-*` 不可移动/删除**：被 **435 个文件**引用（**2026-09-19 记录值**，见 §二 的补注），且 `outputs/full-review-full-20260909/acceptance-ledger.json` 直接指向 `_validation-stage-r16`。它们是复核证据链的一部分。

**可做的只有**：给过程目录加索引说明 + 补 `.gitignore` 规则防止**新增**散文件。

### 🟡 P3 — 被 git 跟踪的备份残留（25 个）

`.gitignore` 写了 `*.bak`，但实际文件名形如 `build_acceptance_ledger.py.bak-20260915-r12`，**不匹配** `*.bak`，因此被正常跟踪入库。

| 位置 | 数量 | 示例 |
|---|---:|---|
| `03-.../2026-09-08-20-30-19/` | 16 | `build_acceptance_ledger.py.bak-20260915-r8…r19` |
| `01-.../docs/host_memory_dump/` | 9 | `review-116-批量通过.csv.bak-20260902` |

**处置**：两种选择——① 保留（它们是复核轮次的原始留档）；② 迁到 `_archive/bak-2026-09/` 并同步引用。**建议①**，仅补 `.gitignore` 规则 `*.bak-*` 防止新增。

> **📌 2026-09-20 执行更正（本节计数与建议均以此为准）**
>
> - **计数有误**：上表两行合计 25 系**巧合**。现测构成为 —— `03-.../2026-09-08-20-30-19/` **16** ✅（正确）、
>   `01-.../docs/host_memory_dump/` **3**（原文记 **9**，❌ 多计 6）、另有 **6** 个位于
>   `03-.../outputs/full-review-full-20260909/`（原文**整段漏列**）。16 + 3 + 6 = **25**。
> - **建议被推翻**：实际采纳的是**建议②**（迁 `_archive/bak-2026-09/`），**不是建议①**。
>   理由：这些留档的**唯一消费方式**是「复现历史轮次基线」，而该需求已由
>   `build_acceptance_ledger.py --round rN` 覆盖 ⇒ 留档退回**纯历史凭证**角色，
>   移出执行层既不改其字节、又不占顶层；`_archive/` 未被 ignore，跟踪状态不变。
> - **执行结果**：25 个 `.bak-*` 全部迁入 `_archive/bak-2026-09/`（**保留原始相对路径**、
>   sha256 逐字节核对 25/25 一致、**不删**）；另 3 个 `_build_ledger_*_baseline.py` 迁
>   `_archive/superseded-20260920/`。全库（排除 `_archive/`）现测 `*.bak-*` = **0**。
> - **引用同步**：`_handoff/skills/项目专用/d10-review-overlay-closeout/SKILL.md`、
>   `_r18_rebuild.py`、`_r19_rebuild.py`、`_p0_ledger_diff.py`、`merge_review.py`、
>   本文件、`REFACTOR-PLAN.md`、`03-.../README-顶层布局与过程目录.md`。
> - **教训**：本节是「**枚举型计数**」失效的又一例 —— 表里两行数字各自有错、合计却正确，
>   若只核合计就会漏掉。**计数类断言必须给出可复跑的现测命令**（判据 > 枚举）。

### 🟡 P4 — 本地可再生垃圾（未入库，但占位）

| 项 | 数量 | git 状态 |
|---|---:|---|
| `__pycache__/` 目录 | 26 | 已 ignore |
| `*.pyc` | 193 | 已 ignore |
| 其它 ignored 项 | 88 | 47 `.log` + 2 `.bak` + 1 `.json` + 38 目录 |

**处置**：本地清理即可，**不影响仓库**。

### 🟢 P5 — 无关空目录

- `01-host-product/2026-08-28-20-59-40/NVIDIA Corporation/umdlogs/`
- 文件数 **0**，内容与项目完全无关（GPU 驱动日志残留）
- 未被 git 跟踪（`git ls-files` 命中 0）
- **处置**：可直接删除（空目录，零风险）

### 🟢 P6 — 01 分区设计文档平铺

`jarvis-agent-design-v0.1.md` / `v0.1-任务分解与派工方案.md` / `v0.1-审核意见.md` / `v0.2.md` / `v0.3.md` 五个文件平铺在分区根。

**处置**：**不动**。`docs/README-版本说明.md` 已做版本对照，且 v0.3 是现行入口，移动会打断 194 个引用。

---

## 四、分级方案

### 第 0 级 —— 只读产出（本次已完成）

- ✅ 本审计文档
- ✅ 索引层已存在：`_index/功能分类总表.md`（10 大类 × 51 子类；文件数**现测**见 `_index/分类统计.json`）
- ✅ 治理层已存在：`GIT-POLICY.md`、`MIGRATION-RECORD.md`、`scripts/git-gate.sh`

### 第 1 级 —— 零破坏清理（建议执行）

| 动作 | 风险 | 需 git 操作 |
|---|---|---|
| 删空目录 `NVIDIA Corporation/umdlogs/` | 无 | 否 |
| 本地清 `__pycache__/` + `*.pyc`（193 个） | 无（可再生） | 否 |
| 补 `.gitignore`：`*.bak-*`、`*.before-*` | 无 | 需提交 |
| 给 03 顶层过程目录加 `_validation-stage-README.md` 说明用途 | 无 | 需提交 |

### 第 2 级 —— 治理补完（**已完成**，2026-09-18 / 09-19）

| 动作 | 状态 | 前置条件 / 结果 |
|---|---|---|
| 移除内嵌孤儿 `.git`（`01-.../2026-08-28-20-59-40/.git/`） | ✅ **已完成**（2026-09-18） | 先验证 subtree 导入完整（633 文件逐一比对）；移除后 01 子树跟踪文件数仍 640、gitlink 0、无 embedded repository 告警 |
| 把 03 顶层 265 个 `.py` 的**索引说明**补进 `_index/功能分类总表.md` | ✅ **已完成**（2026-09-19） | 新增「附：03 分区顶层 265 个脚本怎么读」一节（动词前缀 / 轮次 r1–r19 / 分组 G01–G14 / 波次），**由生成器从活数据产出**，重跑即刷新；详见 §八。⚠️ **本节两处「265」均为 2026-09-19 时点值** —— 该附录**标题里的数字由生成器现算**，此后已随脚本新增而变大 ⇒ **现值看附录标题，不要引用 265** |

> 顺带完成（原未列入）：索引层**整体刷新**（21,115 → 21,143，未归类仍 0）、
> `classify.py` 补 `_handoff/` `scripts/` `.githooks/` 与根层治理文档的归类规则、
> 修正 `make_progress_md.py` 覆盖手写内容的问题。见 §八。

### 第 3 级 —— 明确不做

- ❌ 重命名 `2026-08-28-20-59-40` / `2026-09-08-20-30-19`（引用锚点）
- ❌ 把 `_validation-stage-*` 归入 `_archive/`（435 个引用 + 台账）
- ❌ 按功能大类改建物理文件夹（48,424 处硬编码路径）
- ❌ 安装任何"文件整理器"类 skill

---

## 五、执行纪律

任何第 1/2 级动作必须走既定流程。

> ⚠️ **本节原示例使用 `git switch -c` / `git checkout main`，已于 2026-09-19 更正。**
> 那两条命令在本环境**必被 SIGTERM**，且 SIGTERM 会把工作区文件批量移入回收站 ——
> 本次整理的收口阶段正是因此触发事故（见 §七）。以下为**当前有效**的流程。

```bash
cd /c/Users/<user>/jvs-src

# ① 开分支（等价 git switch -c，但不触碰工作区；分支名必须扁平）
git update-ref refs/heads/chore-org-cleanup "$(git rev-parse main)"
git symbolic-ref HEAD refs/heads/chore-org-cleanup

# ... 改动 ...（长时 git 命令切 ≤3,000 文件/批）

bash scripts/git-gate.sh                 # 门禁全绿
git add -A && git commit -m "chore(repo): 目录整理"

# ② 以 --no-ff 拓扑合入 main
TREE=$(git rev-parse 'HEAD^{tree}')
NEW=$(printf 'merge: chore-org-cleanup\n' | git commit-tree "$TREE" -p main -p HEAD)
git update-ref -m "merge: chore-org-cleanup" refs/heads/main "$NEW"
git symbolic-ref HEAD refs/heads/main
git branch -d chore-org-cleanup
```

**红线**：

1. 不得中断 `git merge` / `git gc` / `git checkout`（见 `GIT-POLICY.md` 第十节事故复盘一）；
2. **禁用 `git switch` / `git checkout` 切分支**（见 `GIT-POLICY.md` 第十一节铁律 1）；
3. **单条 git 命令不超过 ~3,000 个文件**（见 §11 铁律 2）。

---

## 六、执行记录（2026-09-18）

分支：`chore-org-cleanup`（扁平命名）

### ✅ 已执行

| 项 | 动作 | 结果 |
|---|---|---|
| **P1** | 移除 `01-.../2026-08-28-20-59-40/.git/` 内嵌孤儿仓库 | 移除前完成字节级验证（见 `GIT-POLICY.md` 第八节）；原 `.git`（76 文件 / 2.9 MB）移至 `C:\Users\<user>\_staging\embedded-git-backup-20260918\`。移除后 01 子树跟踪文件数仍为 **640**，gitlink **0**，无 embedded repository 告警 |
| **P4** | 清理 `__pycache__` / `*.pyc` | 26 个目录 + 193 个文件（全部已 ignore、0 跟踪） |
| **P5** | 删除空目录 `NVIDIA Corporation/umdlogs/` | 实际清理 **3 处**（01 分区 1 处 + 02 分区 2 处），全部 0 文件、0 跟踪 |
| **P2** | 为 03 分区顶层过程目录补写说明 | 新增 `03-d10-workspace/2026-09-08-20-30-19/README-顶层布局与过程目录.md` |
| — | 交接 schema 升级 | `_handoff/HANDOFF.md` 新增 §10 已否决路线登记表（当时 17 条，**现为 19 条**）、§11 决定状态与进度标记约定、§12 交接记录；§9 状态更新至 2026-09-18 |
| — | 文档同步 | `README.md` 状态表与主线章节、`GIT-POLICY.md` 第八节与变更记录 |

### ❌ 未执行（附理由）

| 项 | 原计划 | 实际处置与理由 |
|---|---|---|
| **P3** | 补 `.gitignore` 规则 `*.bak-*` | **改为不添加。** 那 25 个 `*.bak-*` 是各复核轮次脚本的原始留档，`GIT-POLICY.md` 明确「业务产物一律纳入」，**有意保留跟踪**。加忽略规则会与该项目原则矛盾。改为在 03 分区 README 中说明其性质。**2026-09-20 追加**：25 个留档已迁 `_archive/bak-2026-09/`（**仍被跟踪**、仍未加忽略规则），详见 §P3 的「执行更正」块 |
| **P6** | 01 分区 5 个设计文档平铺 | 维持不动（`docs/README-版本说明.md` 已做版本对照） |
| 第 3 级 | 搬分区 / 归档过程目录 / 装整理器 skill | 明确不做，见第三节 |

### 🔍 附带发现：HANDOFF.md §9 曾经过期

审计过程中发现 `_handoff/HANDOFF.md` §9「如果只剩一件事要做」与 `README.md` 的
「当前主线状态」**停留在 2026-09-16**，写「把 D10 的 10 道留出题跑完」，
而 09-17 / 09-18 实际已完成大量工作（判边 100% 覆盖、图谱构建、10 题生成、
D5 检索层口径定案、H4-a gold 定稿、H01 丢因缺口补抽、146 条边仲裁）。

**已按四态标记重写**，出处见 `HANDOFF.md` §9。

> 这正印证了 §11 记录纪律的必要性：**没有状态标记的交接文档会静默过期**。

---

## 七、事故与收口（2026-09-18 / 09-19）

本次整理在收口阶段遭遇**两次同根因事故**，均已完整还原。
完整复盘见 `GIT-POLICY.md` §10，操作铁律见 §11。

| 次 | 时间 | 触发 | 工作区被移入回收站 | 处置 |
|---|---|---|---|---|
| 1 | 2026-09-18 21:49–21:57 | `git switch main`（后台任务，7m43s 后被杀） | 9,765 个（其中跟踪 9,551） | 定位 → 回收站交叉验证（命中 9,500）→ 从对象库还原 → 逐字节校验 |
| 2 | 2026-09-19 10:04 | `git switch main`（前台，数秒内被杀） | 6,447 个 | 同上，分 3 块有界还原 |

**根因**：本环境对长时 / 阻塞型 git 命令发 **SIGTERM**，而 SIGTERM 会触发宿主机制
把工作区文件**批量移入回收站**。不是 git 的行为 —— 证据是 `.git/logs/HEAD` 里
**没有** `switch main` 的 reflog 条目，且连 `.git/index.lock` 等锁文件**本身也被回收了**。

**零损失**：两次的 `git status` 都只有 ` D`、无任何 ` M`，现存文件内容与 HEAD 一致；
9,551 个文件的 `git hash-object` 结果与 HEAD blob sha1 **逐条一致**；门禁复跑 `OK=15 WARN=1 FAIL=0`。

**收口结果**

| 项 | 状态 |
|---|---|
| 目录整理 | ✅ 已并入 `main`（合并提交 **`5ca4991`**，两父拓扑，等价 `git merge --no-ff`） |
| `chore-org-cleanup` 分支 | ✅ 已删除 |
| 冻结指纹 | ✅ 13/13 一致 |
| `.sse` / `.zip` | ✅ 2363/2363、43/43 |
| 跟踪文件 | ✅ 21,105（`git ls-files` = `git ls-tree -r HEAD`） |

**新增治理条目**

- `GIT-POLICY.md` **§11 环境级铁律**：禁用 `git switch` / `git checkout`；
  长时 git 命令切 **≤3,000 文件/命令**；SIGTERM 后必查陈旧锁、缺失规模、对象库完好性；
  先信对象库、再信回收站。
- `HANDOFF.md` **§10 R18 / R19**：把上述两条登记为**永久禁忌**（附实测数据与重议条件）。

> **本环境最重要的一条经验**：`git switch` 不是「慢」，是**必被杀**；
> 而杀掉它会连带把工作区文件移进回收站。任何「切分支」的念头都应立刻改为
> `git symbolic-ref HEAD` + `commit-tree` / `update-ref`，全程不触碰工作区。

---

## 八、索引层刷新与脚本说明（2026-09-19）

收口后执行第 2 级剩余项，顺带把索引层从「09-16 快照」刷新到实存状态。

### 做了什么

| # | 动作 | 结果 |
|---|---|---|
| 1 | **新增「03 分区顶层 265 个脚本怎么读」** 到 `_index/功能分类总表.md` | 覆盖命名三段式、31 种动词前缀、r1–r19 轮次分布、G01–G14 分组分布、非规范命名清单。⚠️ **「265」是 2026-09-19 时点值**，附录标题由生成器现算 |
| 2 | **索引层整体刷新** | 21,115 → **21,143** 个文件；未归类仍 **0** |
| 3 | `classify.py` 补规则 | 新增子类 `接手包与技能`（`_handoff/`）、`门禁与钩子`（`scripts/` + `.githooks/`）；`仓库配置` 纳入 `GIT-POLICY.md`；`项目审计` 纳入 `ORGANIZATION-AUDIT.md` |
| 4 | `status_map.py` 同步 | 两个新子类登记为「已开发」，避免木桶口径把 09 大类拖成「待规划」 |
| 5 | 生成器缺陷修复 | 徽章渲染（`开发中`/`待开发`/`待规划` 前导空格导致表格错位）→ 正确 emoji；生成日期改为动态；`## ️` → `## ⚠️` |
| 6 | **修复生成器覆盖手写内容** | `_handoff/PENDING.md` 原有的整节「git 治理状态」**只存在于产物、不在生成器中**，重跑即被静默抹掉。已把该节（并按 09-19 事实更新：4 个标签 / 39 提交 / 两条硬禁忌）搬进生成器，并在产物尾部加「自动生成，手写会被覆盖」警示 |
| 7 | 数字口径修正 | 03 顶层目录 27 → **26**、过程目录 23 → **22**、`.txt/.md` 6 → **7**（4 txt + 3 md）、跟踪文件 21,104 → **21,105** |

### 关键设计：脚本说明**由生成器产出**

新增章节不是手写死文本 —— `make_index_md.py` 的 `write_top_scripts()` 每次重跑都**现场扫描**
`03-.../2026-09-08-20-30-19/` 顶层的 `.py`，重新统计前缀 / 轮次 / 分组。
所以脚本增删后重跑，说明自动跟着变，不会过期。

人工登记的部分只有一处：`VERB_ROLE` 字典（前缀 → 中文作用）。新增前缀若未登记，
表里会显示 `—`，这是**有意的提示**，提醒补登记。

### 校验

> ⚠️ **本节已改为「重测命令」而非写死数值**（2026-09-19 第二次修正）。
> 原因：下面的数字**每次提交都会变**，而写死值会**静默过期**。
> 上一版写的「21,143 / 21,195 / 排除 52」在当时正确，但本节保留的是值而非命令，
> 于是它们在本轮提交后即失效 —— 属本项目「静默过期」家族的又一实例。
> **值会过期，命令不会。**

```bash
cd /c/Users/<user>/jvs-src
wc -l < _index/文件索引.jsonl        # 索引覆盖数
git ls-files | wc -l                 # git 跟踪文件数
python -c "import json,io;print(sum(1 for l in io.open('_index/文件索引.jsonl',encoding='utf-8') if l.strip()))"
grep -c '"l1"' _index/文件索引.jsonl  # 与上条应一致
```

| 项 | 判据（不是数值） |
|---|---|
| 未归类 | 必须为 **0** —— `_index/分类统计.json` 的 `unclassified` |
| 子类求和 = 总数 | 必须相等 —— `_index/分类统计.json` |
| 索引里的陈旧条目（索引有、磁盘无） | 必须为 **0** —— 见技能 `jvs-index-layer-maintenance` 校验段 |
| 扫描口径 | 排除 `_migrate/`、`_index/`、`.workbuddy-ai/`、`.git/`、`__pycache__/`、`node_modules/` |

> ⚠️ **口径提醒：索引覆盖数 ≠ 物理文件数，且两者都不要与「`git ls-files` 跟踪数」相减。**
> 三者**不是同一件事**：
> - **索引覆盖数** = `os.walk` **不跟随 link** + 排除上述目录
> - **跟踪文件数** = `git ls-files` = `git ls-tree -r HEAD`
> - **物理全盘数** = ⚠️ **口径不稳定，勿引用**（随工具与 24 个 junction 漂移：
>   `find` 与 `os.walk` 结果不同，因为 `os.path.islink` 对本机 junction **全返 False**）
>
> 「git 跟踪范围」与「索引排除范围」是**两套独立规则**
> （`04-restricted-materials/` 磁盘 34 个文件**完全未跟踪**；
> `.workbuddy-ai/` 的部分文件**被跟踪**）⇒ **不要用一个减法串起来**。

> 📌 **2026-09-19 本轮实测快照**（**仅作参考，勿当常量引用**）：
> 索引覆盖 **21,157** ｜ 跟踪文件 **21,119**。
> 排除项分解（本轮实测）：`_migrate/` 28 + `.workbuddy-ai/` 7 + `__pycache__/`（01 `tools/causal`）4 + `_index/` 4 = **43**。

### 维护六步（顺序不可换）

```bash
cd /c/Users/<user>/jvs-src
# ⓪ 先改规则，再「先 add 后生成」（生成物含「是否入库」标记，见技能坑 12）
# ① 改分类规则 _migrate/classify.py（RULES）
# ② 新子类必须登记 _migrate/status_map.py（STATUS / PENDING），否则回落「待规划」
git add -A                           # ← 必须在跑生成器之前
python _migrate/classify.py          # -> 文件索引.jsonl + 分类统计.json
python _migrate/make_progress_md.py  # -> 功能进度表.md + _handoff/PENDING.md
python _migrate/make_scripts_ledger.py  # -> _index/一次性脚本登记表.md
python _migrate/make_scripts_md.py      # -> _handoff/scripts/SCRIPTS.md
python _migrate/classify.py          # ← 必须再跑一次，见下
python _migrate/make_index_md.py     # -> 功能分类总表.md
git add -A                           # 生成物是新文件/已改，再 add 一次
```

> ⚠️ **`classify.py` 必须跑两次**（2026-09-19 修正）。
> `classify.py` 会**索引** `_handoff/PENDING.md` 与 `_handoff/scripts/SCRIPTS.md`，
> 而这两个都是生成器的**产物** ⇒ 若 `classify.py` 只跑一次，它记录的是**上一轮的**尺寸，**每轮滞后一拍**。
> **判据**：凡产物落在「会被索引」路径（`_handoff/`、`03-`…）的生成器，都必须排在
> 最后一次 `classify.py` **之前**；产物落在 `_index/` 的（被显式排除）位置自由。
> 本节原写「维护三步」并给出 `classify → make_index_md → make_progress_md` 的顺序，
> 该顺序会**稳定产出陈旧尺寸**，**已作废**。
> 完整证据链见 `_handoff/skills/项目专用/jvs-index-layer-maintenance/references/坑清单-B-校验与断言.md` 坑 6（2026-09-20 细分后该坑正文移入附页）。
