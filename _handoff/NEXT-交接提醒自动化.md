# 任务书：给 JVS 的「交接提醒」加自动化

> 生成 2026-09-19 ｜ 产出于「补齐 `project-handoff` 配套技能」会话
> 用户裁定：**把能力缺口带走，另开新对话推进**
> 项目根：`C:\Users\<user>\Desktop\JVS`
> 本文**自包含** —— 新对话无需先读整个仓库，按 §1–§9 执行即可

---

## 0. 一句话目标

把 JVS 的跨会话**交接提醒**，从「纯人工自检」升级为「**可自动触发**」，
且**不破坏**既有的三动作分离（提醒 ≠ 保存 ≠ 新建接续）与去重冷却纪律。

⚠️ **本任务必然要改一条现行纪律**（§2 缺口 4）。改纪律是本任务的**前置条件**，
不是附带副作用 —— 请先取得用户对「放宽该条」的明确授权，再动手。

---

## 1. 现状：今天的口径是什么

| 项 | 现状 |
|---|---|
| 有没有自动提醒 | **没有** |
| 实际口径 | Agent 在**阶段收尾时自检** + 结论落 `_handoff/HANDOFF.md` §12「评估登记（自检留痕）」表 |
| 触发门槛 | 「≥3 次自动压缩后」—— **无法自动执行**，靠 Agent 人工判断 |
| 配套技能 | `jvs-project-handoff`（2026-09-19 新建；用户级 + 项目级双副本） |
| 上游来源 | `duoduoler-ops/Table-skills` 的 `project-handoff`（MIT）——**方法层已移植（SKILL.md 正文 + `references/handoff.md` 附页）；机制层 4 项未移植**（`references/compaction-reminder.md` / `scripts/compaction_reminder.py` / `hooks/` / `tests/`），见 `HANDOFF.md` §10 **R16** 与 `ORGANIZATION-AUDIT.md` §一 |
| **上游机制层副本** | `C:\Users\<user>\_staging\upstream-table-skills\`（2026-09-19 拉取，**本任务的实现参考**：473 行状态机脚本 + 508 行单测 + Hook 模板 + 机制层附页） |

**关键点**：现在的提醒**完全依赖 Agent 自觉**。若某轮收尾时忘了自检，
不会报错、不会有任何提示 —— 属项目一直在清的「**静默过期**」家族。

---

## 2. 能力缺口清单（本任务要填的就是这些）

### 缺口 1：宿主没有「自动压缩」这个**事件**
上游的自动提醒挂在**自动压缩检查点**上（压缩发生时触发 Hook）。
本宿主**不提供压缩事件、不提供 Hook** ⇒ 无法在「压缩发生的那一刻」执行任何逻辑。

### 缺口 2：宿主没有**压缩计数器**
纪律里的门槛是「首次到**第 3 次**自动压缩后」「距上次提醒**新增至少 3 次**压缩」。
本宿主无计数器 ⇒ 这两个数字**无从取得**。

### 缺口 3：上游机制层依赖的工具 —— **本宿主「部分等价」，非「完全没有」**
（⚠️ **2026-09-19 更正** —— 原文写「本宿主没有的工具」**已不准确**）

- CLI 接口：`prepare` / `evaluate --outcome defer|skip` / `next_turn` / `respond defer`
  —— ❌ **仍缺**（这是上游脚本自带的子命令，与本宿主能力无关，**移植脚本即得**）
- 跨会话寻址：`create_thread` / `list_projects` / `send_message_to_thread` / `fork_thread`
  —— ⚠️ **部分等价，且成品桥已交付**：本机 DSH `--profile acp` 提供
  `session/new` / `session/prompt` / `session/list` / `session/resume`，**逐项对应**
  （零安装：DSH 与 `@agentclientprotocol/sdk` v1.4.0 均已就位，Apache-2.0）。
  ⭐ **成品桥** = `01-host-product/2026-08-28-20-59-40/tools/host/acp_bridge.py` + `README-acp-bridge.md`。
  **不覆盖**：分叉（`session/fork` **实测 `-32601` 不可用**）、GUI 打开、跨位置移交
  （上游 `navigate_to_codex_page` / `handoff_thread` 无对应物）
- 上游提醒脚本还**硬编码 Codex 路径与 `CODEX_HOME`** —— ✅ 属**改写工作量**，非能力缺失

> 📋 **勘察报告**：`01-host-product/2026-08-28-20-59-40/docs/host_memory_dump/M1-10-跨会话寻址工具勘察-20260919.md`
> ⚠️ **但不得据此宣称「机制层可 1:1 复刻」** —— 见方案 D 的三个卡点，其中
> **「接手方先只读核验」在 ACP 下只能靠开场白软约束**（上游自动接续 6 步中 2 步完整、4 步部分）。

### 缺口 4（**本任务的核心**）：项目自己的纪律**明令禁止**
`_handoff/HANDOFF.md` §11「边界纪律」原文：

> 不写入全局路由、**不添加定时任务**、不修改压缩阈值。

这条是从上游方法层一并采纳的。**要做自动化，必须先修订这一条** ——
否则就是「纪律说禁止、实现却在做」，比没有更糟。

### 缺口 5：**本环境其实是支持定时任务的**（所以缺口 4 不是技术限制，是纪律限制）
- 本宿主提供定时任务能力：一次性 + 周期性（RRULE：HOURLY / DAILY / WEEKLY / MONTHLY / YEARLY）
- **现成实例**（可直接参照）：
  - 名称：`D10 补抽+判分：探测配额恢复即执行`
  - id：`12ef0bf8-4da8-4eee-bc03-36588c3fbb52`
  - 周期：每小时的第 0 / 20 / 40 分（即 `FREQ=HOURLY;INTERVAL=1;BYMINUTE=0,20,40`）
- **能力边界（务必认清）**：定时任务只能**按时间/间隔轮询**，
  **无法绑定到「压缩事件」**。所以只能做**近似**，不能 1:1 复刻上游。

---

## 3. 可用能力（新对话可直接调用）

| 能力 | 说明 |
|---|---|
| 定时任务创建 | 支持 `once`（指定时刻）与 `recurring`（RRULE）。周期性必须给全 FREQ 及该频率的必填字段 |
| 任务 prompt 写法 | **必须自包含**：未来运行看不到当前对话。**不要**把时间写进 prompt（时间进 rrule、目录进 cwds） |
| 任务数量纪律 | **一条逻辑任务只建一条**，不要把同一件事拆成多条 |
| 读文件 | 定时任务运行时可以读仓库文件（如 §12 评估登记表、`git log`），据此决定「是否真的提醒」 |
| 写文件 | 可写状态文件实现**去重冷却**（见方案 B） |

> ⚠️ **不要**在 prompt 里放「每天 9 点」这类时间描述；也不要放工作目录。
> 也不要向用户展示原始 RRULE 字符串，用自然语言描述周期。

---

## 4. 需要同步修改的落点（一处纪律变更会牵动多处）

| 文件 | 改什么 | 为什么 |
|---|---|---|
| `_handoff/HANDOFF.md` §11「边界纪律」 | 「**不添加定时任务**」→ 改为**有条件允许**（写明允许范围与禁止范围） | 本任务的前置条件；不改就是自相矛盾 |
| `_handoff/HANDOFF.md` §12「能力缺口」段 | 缺口 1/2 被部分填补，须**如实更新**（不能宣称已完全解决） | 该段明确写「**不得宣称有程序兜底**」 |
| `_handoff/skills/项目专用/jvs-project-handoff/SKILL.md` | §2「能力缺口」+ §8「边界纪律」同步 | 技能与 HANDOFF 必须一致 |
| `C:\Users\<user>\.workbuddy-ai\skills\jvs-project-handoff\SKILL.md` | 同上（**用户级权威副本**） | 两副本不自动同步，必须 `cp` + `diff` |
| `ORGANIZATION-AUDIT.md` §一 | 「能力缺口已如实登记」行更新 | 该行是本项目对外说明「为什么不整体安装上游 skill」的依据 |
| `_migrate/status_map.py` | 新待办登记 / 完成后移入 `CLOSED_<日期>` | 「裁定 → 待办 联动」纪律 |
| `.workbuddy-ai/memory/MEMORY.md` 第 22 条 | 「无自动提醒」的实际口径需更新 | 该条目前写死「无自动提醒」 |
| `_index/`（4 个文件） | 改完**必须重跑生成器** | 被索引文件一改，尺寸字段即陈旧 |

> ⚠️ **2026-09-19 追加落点**（勘察结论直接改动三处，勿漏）：
> ① `_handoff/HANDOFF.md` §10 **R16 两列**（重议条件 + 否决理由）；
> ② 本文件 §2 **缺口 3**（「本宿主没有」→「部分等价」）+ §5 新增**方案 D**；
> ③ `01-.../host_memory_dump/M1-10-跨会话寻址工具勘察-20260919.md`（**新文件**，须进索引）。
> 若采用方案 D，另需同步：`_migrate/status_map.py` 登记派生待办、`ORGANIZATION-AUDIT.md` 评估行、
> 两副本技能 `jvs-project-handoff` 的能力缺口节。

---

## 5. 四个备选方案

### 方案 A：纯定时巡检（最小改动）
定时任务（如每日一次）读 `_handoff/HANDOFF.md` §12 评估登记表 + `git log`，
判断「距上次评估是否已隔很久 / 是否有未登记的阶段收尾」，满足则提醒。

- ✅ 改动小、无新文件
- ❌ **无法感知压缩次数**，只能用**时间间隔**近似 ⇒ 与纪律里的「3 次压缩」门槛**口径不一致**
- ❌ 去重只能靠「读登记表」推断，容易重复提醒

### 方案 B：状态文件 + 定时巡检（**推荐**）
新增状态文件（如 `_handoff/.handoff-state.json`），记录：

```json
{
  "last_reminded_at": "2026-09-19T15:40:00+08:00",
  "last_assessed_at": "2026-09-19T15:40:00+08:00",
  "last_stage_key": "<阶段指纹>",
  "cooldown_until": null,
  "outcome_log": ["skip", "defer", "prepare"]
}
```

定时任务读它 → 判断是否满足提醒条件 → 提醒后写回 → 真正实现**去重冷却**。

- ✅ 能落实「同阶段同一具体混淆只提醒一次」「用户回应后冷却」两条纪律
- ✅ 可审计（状态文件进 git，谁改的、何时改的可查）
- ⚠️ 必须处理**并发写**与**文件不存在**的首次运行
- ⚠️ 「3 次压缩」仍需**改口径**（见下）

### 方案 C：不碰宿主机制，只补「自检触发清单」
把「什么情况该评估交接」写成 §12 里一张**可机械对照的清单**
（阶段收尾 / 关键裁定落盘后 / 上下文明显混淆时 …），降低漏检。

- ✅ 零风险、零纪律冲突
- ❌ **仍然不是自动**，只是让自检更容易被执行

### 方案 D：ACP 桥（**本机可编程创建并投递新会话**）

> **2026-09-19 新增**，依据 = 只读勘察报告
> `M1-10-跨会话寻址工具勘察-20260919.md`。
> 前置结论：**GitHub 上没有等价物，但本机 DSH 的 `--profile acp` 就是等价底座。**
>
> ⭐ **2026-09-19 17:30 更新：底座已端到端实测打通**（报告 §九，十项全绿）——
> 握手 / `session/new` / `session/prompt`（模型真实回复）/ `session/list` / 落盘 / `session/close` 全部可用。
> ⭐ **成品桥已交付并实测**（2026-09-19）：
> `01-host-product/2026-08-28-20-59-40/tools/host/acp_bridge.py`（零依赖，25 KB）+ `README-acp-bridge.md`。
> 实测**新建 / 投递 / 续投 / 软约束生效 / 闸门拦截 / fail-closed** 全通过。
> ⚠️ **但仍是半成品**：与 `HANDOFF.md` §12 登记表**未自动联动**（登记行仍靠人写），
> 且**触发条件仍无解**（缺口 1/2）⇒ `status_map` 中方案 D 记「**开发中**」。

用 `dsh --profile acp`（ACP stdio）+ `@agentclientprotocol/sdk` v1.4.0，实现四件事：

| 动作 | ACP 方法 | 对应上游 | 状态 |
|---|---|---|---|
| 创建新会话 | `session/new`（必需 `cwd`, `mcpServers`）→ 返回 `sessionId` | `create_thread` | ✅ 已实现 |
| 投递开场白 | `session/prompt`（`sessionId`, `prompt`） | `send_message_to_thread` | ✅ 已实现 |
| 列举 | `session/list`（可按 `cwd` 过滤） | `list_projects` | ✅ 已实现 |
| 恢复 | `session/resume` | — | ✅ 已实现（**保留历史**，见卡点 3） |
| ~~分叉~~ | ~~`session/fork`~~ | ~~`fork_thread`~~ | ❌ **实测不可用**（`-32601`） |

- ✅ **零安装** —— DSH 已装（`dsh-install-4` = 0.1.5-rc.1）、ACP profile **随包发布**
  （`@deepseek-ai/dsh-acp-app`；⚠️ **不是**因为 `~/.dsh/profiles/acp` 存在 —— 该目录**并不存在**，
  profile 从安装包解析）、SDK 已在 `~/.dsh/profiles/node_modules/`；**不需要安装任何东西**
  （实测细节见报告 §9.2）
- ✅ **可编程、可审计** —— 会话落盘 `~/.dsh/sessions/<workspace-key>/<uuid>/`，
  **实测两种格式并存**：`session.v3.jsonl.zstd`（新建会话用这个）与 `session.jsonl.zstd`；
  只新增一个本地状态文件 + 一个薄客户端脚本，**不推不删不改配置**
- ⚠️ **卡点 1（最关键）**：**不能强制新会话「先只读核验」** —— ACP 无「约定首轮行为」机制，
  上游自动接续第 3 步只能靠**开场白文本软约束**。⇒ **不得宣称已复刻上游机制**
- ⚠️ **卡点 2**：无 GUI 打开、无跨位置移交（上游 `navigate_to_codex_page` / `handoff_thread` 无对应物）
- ⚠️ **卡点 3（2026-09-19 实测更正）**：~~`session/resume` 不重放历史~~ —— **实测证否**。
  决定性实验：告知随机口令「蓝鲸-7419」→ 关闭 → `resume` → **不给口令再问** → 模型**准确复述**。
  ⇒ **resume 保留对话历史**；README 原句「不重放历史」说的是**不重放 MCP 声明**，此前被误读。
  ⚠️ **但仍不应依赖它做交接**（无文档承诺、跨版本可能变）⇒ **材料仍须自包含**。
- ⚠️ **卡点 4**：寻址的是 **DSH 会话**，不是 WorkBuddy 会话；**权限边界未评估**（谁在哪个目录下跑）
- ⚠️ **卡点 5（2026-09-19 实测定论）**：**`session/fork` 确证不可用** —— 直调返回
  `-32601 Method not found`；能力声明只有 `{close, list, resume}`。
  ⇒ 报告 §3.3 映射表**已下调**（`fork_thread` 由 ✅ 改 ❌）；桥**不提供 fork 子命令**。
  教训：**读 schema 只能确认方法存在，不能确认被实现。**
- ⚠️ **卡点 6（2026-09-19 实测新增）**：`session/list` **不返回全部磁盘会话**
  （实测：磁盘 68 个目录 vs `session/list` 33 条；`workspace.json` 的 `archivedSessionIds` 有 16 条，
  **是部分原因但不足以完全解释**）⇒ 不能把 `session/list` 当「会话全集」用，
  **精确口径未定，勿在报告里写死数字**。
- ~~⚠️ **卡点 7（2026-09-19 实测新增，影响最大）**：**ACP 会话内 git 不可执行** ——
  接续方实测回报：该会话 shell 是 **PowerShell 5.1**，其 **`PATHEXT` = `.CPL`（不含 `.EXE`）**
  ⇒ `git` / `git.exe` / 绝对路径**全部无输出**。
  **后果**：接手方**无法提交**，而本环境**不提交的改动会被回滚** ⇒
  **凡需落库的改动不能在 ACP 会话内完成**，必须回到 WorkBuddy 侧。~~
  → ✅ **2026-09-19 同日已定位根因并修复，本条不再是卡点**：根因 = **父进程未设 `PATHEXT` 时
  PS 5.1 自身 fallback 到 `.CPL`**（**与 PS 版本无关**；机器级注册表值正常、用户级不存在）。
  ⚠️ **绝对路径不能绕过**（坏 PATHEXT 下静默失败、退出码 0）。**桥已在启动 DSH 时注入正常值**，
  实测接续方侧 `git version 2.55.0.windows.3`、`git status` 可用（报告 §9.6.4）。
  ⚠️ **手动起 DSH（不经桥）仍会复现**。投递材料**不再需要**写这条；落库收尾仍**建议**回 WorkBuddy 侧
  （便于统一门禁口径），但**不是硬限制**。
- ⚠️ **卡点 8（2026-09-19 实测新增）**：`session/resume` **不能对活跃会话调用**
  （报「session 已活跃」）⇒ 需先 `--close` 再 resume。
- ⚠️ **做「自动触发」仍需先修订 §11**（同方案 A/B，见缺口 4）；
  但**「用户主动要求交接 → 程序代为创建并投递」这条路径不需要定时任务**，**已可直接落地**
  （成品桥已交付 ⇒ 此路径**可执行**，只是登记联动仍靠人写）

> **不改的事项**：R16 **仍不整体安装**上游 skill（机制层依赖 **Hook 事件**，与「有没有寻址工具」
> 是两件独立的事，勿混）；上游 `compaction_reminder.py` 等 4 个文件**仍不可用**。
> ⚠️ **「底座已打通」不等于「方案已完成」** —— 实测打通的是 DSH ACP 底座，
> 桥成品代码与交接流程接线**都还没有**（详见报告 §9.4）。

### 结论：为什么方案 D ≠ 缺口 1/2 的解法

方案 D 解决的是「**有了交接材料之后，能不能自动找到并投递到新会话**」；
它**不解决**「**什么时候该提醒**」（那需要压缩事件 / 计数器 —— 缺口 1/2 仍无解）。
⇒ **方案 D 与方案 B/C 互补，不可互相替代。**

### 关于「3 次压缩」门槛的口径（**必须让用户裁定**）
本宿主无计数器，只有三条路：
1. **改门槛**为可观测的量（如「同一阶段内累计 N 轮实质交付」或「距上次评估 ≥N 天」）
2. **保留门槛但标注为人工估计**（承认无法精确）
3. 用状态文件记录**轮数**近似压缩次数（不准，但可复现）

---

## 6. 验收标准（缺一不算完成）

1. **定时任务被实测触发过至少一次** —— 不能「创建了就报完成」
2. **去重冷却可验证**：同一阶段连续触发**只提醒一次**（要有实测记录，不是「设计上应该」）
3. **§11 边界纪律已同步修订** —— 不能出现「纪律禁止 / 实现照做」
4. **技能两副本 `diff` 一致**（用户级 + 项目级）
5. `ORGANIZATION-AUDIT.md` 能力缺口行 + `MEMORY.md` 第 22 条已更新
6. 索引层**六步生成器**重跑（顺序见 §8），集合差校验「消失 = 0」
7. 门禁 **OK / WARN=0 / FAIL=0**，合并走 plumbing，分支已删
8. **如实登记仍未填补的缺口** —— 不允许因为「做了一半」就宣称有程序兜底

---

## 7. 环境禁忌（⚠️ 不遵守会**丢文件**，不是吓唬）

| 禁忌 | 原因 | 改用 |
|---|---|---|
| **绝不** `git switch` / `git checkout` | 本环境对这两个命令**必发 SIGTERM**，而 SIGTERM 会把工作区文件**批量移入回收站**（09-18 回收 9,765 个 / 09-19 回收 6,447 个） | `git update-ref` + `git symbolic-ref`；取回文件用 `git restore --source=HEAD -- <path>` |
| **绝不中断** `git merge` / `git gc` / `git checkout` | 长时无输出 ≠ 卡死；强杀会丢 `.git/refs` 与 pack | 命令**本身切小**（≤3,000 文件/命令），放后台观察 |
| 工作区内改动**可能被环境回滚** | 回滚粒度**不是整文件**（同文件一处被还原、另两处保留） | 每次编辑后**立即回读**；「改文件 + `git add` + `git commit`」压在**单次运行内** |
| 提交 ≠ 捕获最终态 | 实测：某提交声称「3 份副本已同步」，库里其实 `git ls-tree` 返回 **0 文件**（从未 `git add`） | **提交后读 HEAD 复核**：`git show HEAD:<path> \| grep -F "<串>"` + `git ls-tree -r HEAD -- <新目录>` |
| 检查串凭印象写 | 一轮内连中 **3 次假阴性**（报 FAIL 而文本正确） | 先 `grep -o` / `sed -n` **打印真实行**再照抄；一律 `grep -F`，**不用正则** |

完整流程见技能 **`git-sigterm-safe-recovery`**。

---

## 8. 收口纪律（每轮收尾固定动作）

```bash
# 1) 开分支（扁平名，禁斜杠；本环境 git branch feat/x 会静默失败）
git update-ref refs/heads/<new-branch> "$(git rev-parse HEAD)"
git symbolic-ref HEAD refs/heads/<new-branch>

# 2) 改文件 + add + commit 压在一次运行内
git add <files> && git commit -F - <<'EOF'
...
EOF

# 3) 提交后回读 HEAD（不是工作区）
git show HEAD:<path> | grep -F "<关键串>"
git ls-tree -r --name-only HEAD -- <新目录> | wc -l   # 必须 > 0

# 4) 门禁（全绿才允许合并）
bash scripts/git-gate.sh        # 判据：FAIL=0 且 WARN=0；自报 OK 数 == 输出中 [OK] 行数（勿照抄数字，见 GIT-POLICY.md §五）
bash scripts/scan-secrets.sh

# 5) 索引层六步（顺序不可打乱，且必须先 add）
#    判据：产物落在「会被索引」路径（_handoff/、03-…）的生成器，必须排在最后一次 classify 之前
#      - PENDING.md   ← make_progress_md.py
#      - SCRIPTS.md   ← make_scripts_md.py
#    产物落在 _index/（classify 显式排除）的位置自由
#    先 add 的原因：产物内嵌「是否入库」标记（git ls-files 推导）
git add -A
python _migrate/classify.py
python _migrate/make_progress_md.py
python _migrate/make_scripts_ledger.py
python _migrate/make_scripts_md.py
python _migrate/classify.py
python _migrate/make_index_md.py
git add -A

# 6) plumbing 等价合并（生成与 --no-ff 一致的两父拓扑）
TREE=$(git rev-parse <branch>^{tree})
SHA=$(git commit-tree "$TREE" -p main -p <branch> -F -)
git update-ref -m "merge <branch>" refs/heads/main "$SHA"
git update-ref -m "merge <branch>" refs/heads/main "$SHA"   # no-op 补 reflog 说明
git symbolic-ref HEAD refs/heads/main
git update-ref -d refs/heads/<branch>
```

**Python**：`C:\Users\<user>\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe`

---

## 9. 关键文件索引

| 用途 | 路径 |
|---|---|
| **接手入口（先读这个）** | `_handoff/HANDOFF.md`（§11 授权边界/展示规范/边界纪律；§12 评估登记/去重冷却/能力缺口） |
| 配套技能（权威副本） | `C:\Users\<user>\.workbuddy-ai\skills\jvs-project-handoff\SKILL.md` |
| 配套技能（项目副本） | `_handoff/skills/项目专用/jvs-project-handoff/SKILL.md` |
| 已否决路线登记（R16） | `_handoff/HANDOFF.md` §10（重议条件 **2026-09-19 改「部分满足」**） |
| **跨会话寻址勘察报告** | `01-host-product/2026-08-28-20-59-40/docs/host_memory_dump/M1-10-跨会话寻址工具勘察-20260919.md` |
| 上游评估结论 | `ORGANIZATION-AUDIT.md` §一「补充评估：`duoduoler-ops/Table-skills` 的 `project-handoff`」 |
| 状态/待办单一真源 | `_migrate/status_map.py`（`STATUS` / `PENDING` / `CLOSED_<日期>`） |
| 项目长期记忆 | `.workbuddy-ai/memory/MEMORY.md`（第 22 条 = 交接与提醒纪律） |
| 当日日志 | `.workbuddy-ai/memory/2026-09-19.md`（§十七 = 本轮） |
| 环境禁忌技能 | `_handoff/skills/通用/git-sigterm-safe-recovery/SKILL.md` |
| git 规则原文 | `GIT-POLICY.md` |

---

## 10. 交接提醒的三个动作（**不要混为一谈**）

`_handoff/HANDOFF.md` §11 原文纪律，本任务**不得破坏**：

| 动作 | 触发 | 不得自动扩展为 |
|---|---|---|
| **提醒** | 到检查点且当前工作已安全收拢 | 不自动保存 |
| **保存** | 用户明确要求保存 / 整理交接 | 不自动新建任务 |
| **新建接续** | 用户明确要求「新建并继续」 | —— |

- 笼统的「交接一下」**不**扩展为新建任务
- 同一次明确授权**不**逐步重问
- 用户未回应时**继续已授权的工作**，不追问、不自动保存或新建任务
- 提醒展示规范：以 **`交接建议：`** 开头，放**最终答复正文最前、单独一段**，
  **不进**引用块 / 示例 / 代码块

> 本任务要自动化的是**「提醒」这一个动作**，**不是**保存、更**不是**新建接续。
