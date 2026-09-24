# JVS —— 时序-因果图谱 + 跨会话因果问答

> **这是 JVS 的公开层**：只含**代码、工具链、提示词、治理方法论与技能文档**。
> 语料、评测产物、执行过程材料属**私有层**，不随本仓库发布（见「公开层 / 私有层」一节）。

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

- **许可证**：GNU Affero General Public License v3.0（见 [`LICENSE`](LICENSE)）
- **建立于**：2026-09-16

---

## 这个项目是什么

**M1「跨会话因果问答」MVP**。宿主产品是贾维斯类个人 Agent，只做一件事：

> **时序-因果图谱 + 跨会话因果问答**

两个关键设计文档在公开层内：

| 文档 | 内容 |
|---|---|
| `01-host-product/2026-08-28-20-59-40/jarvis-agent-design-v0.3.md` | 设计现行版 |
| `01-host-product/2026-08-28-20-59-40/docs/M1-立项书-v0.6.md` | 立项基线（已签字） |

### 因果层是唯一自研核心

评测、采集、宿主工具都是**围绕**因果层的外围。因果层本身的职责是：
把非结构化事件流编译成「**带方向、带强度、可追溯**」的因果边，
并支撑「跨会话、跨时间」的问答。

---

## 公开层 / 私有层

本仓库是**拆库**的产物：同一套系统按「**能否公开**」切成两层。

| | 公开层（本仓库） | 私有层（不发布） |
|---|---|---|
| 内容 | 代码、工具链、提示词、治理方法论、技能 | 语料衍生品、评测产物、执行过程材料、身份表 |
| 分目录 | `01-host-product/.../tools/`、`_handoff/`、`_migrate/`、`scripts/` | `02-m1-evaluation/`、`03-d10-workspace/`、`04-restricted-materials/` |
| 原因 | —— | 含真实姓名 / 业务标识 / 未脱敏语料 |

> ⚠️ **本仓库内的文档会提到 `02-` / `03-` / `04-` 等私有分区路径** ——
> 那是**为了说明系统结构**（哪些内容在私有层），
> **不是**「克隆本仓库就能找到这些文件」。缺失属**预期**，不是构建错误。
>
> ⚠️ 同理，某些脚本（尤其 `_migrate/`）的用法**依赖私有层存在**。
> 它们在公开层**单跑会报错/断链** —— 这是拆库的固有结果。
> 详见下方「已知限制」。

---

## 🚀 快速开始（只依赖公开层内自洽的内容）

### 1. 环境要求

- **Python 3.13+**（本项目开发期用 3.13.12 核验）
- **Git**（部分脚本用 `git grep` / `git ls-files`）
- **bash**（Windows 下用 Git Bash）

> 本项目的门禁脚本里解释器路径**可覆盖**：设环境变量 `JVS_PY` 即换机可用。
> 不设时使用脚本内的默认值。

### 2. 跑离线回归（不调任何模型）

工具链的关键回归件都在公开层内，**无需私有语料即可运行**：

```bash
cd 01-host-product/2026-08-28-20-59-40/tools/causal

python -B test_llm_judge.py           # 判分器回归
python -B test_causal_graph.py        # 因果层入库闸门回归
python -B test_llm_answer_render.py   # 提示词渲染契约回归
```

三者都以末行打印 `OK` 为通过。

### 3. 隐私闸门（真名扫描）

```bash
cd 01-host-product/2026-08-28-20-59-40/tools/host

# ⭐ 必须提供词表 —— 本仓库不含任何真实姓名
python name_scan.py --check <目标> --names names-example.json
```

- `names-example.json` 是**虚构**示例词表，演示结构；
- **未提供词表 / 词表为空 ⇒ 主动报错并返回 2（拒绝运行）** ——
  因为「空词表 ⇒ 扫描通过」会输出**虚假的干净结论**，这比报错危险得多；
- 退出码：`0`=放行 ｜ `1`=命中拦截 ｜ `2`=输入或词表错误。

### 4. 凭证扫描

```bash
bash scripts/scan-secrets.sh
```

### 5. 启用提交守卫（一次性）

`.githooks/` 下的钩子**不会随克隆自动生效**，需手动指向一次：

```bash
git config core.hooksPath .githooks
```

之后每次 `git commit` 都会先跑 `.githooks/pre-commit`（工作区状态、语法核验、
红线守卫等）。不想启用时 `git config --unset core.hooksPath` 即可。

> ⚠️ 若你的解释器不在默认路径，设 `JVS_PY` 指向它，否则守卫会报「未探测到可用解释器」。

---

## 目录总览（公开层实际内容）

| 目录 | 内容 |
|---|---|
| `01-host-product/2026-08-28-20-59-40/tools/` | **工具链**：因果管线、评测、宿主工具、隐私闸门 |
| `01-host-product/2026-08-28-20-59-40/prompts/` | LLM 提示词模板 |
| `01-host-product/2026-08-28-20-59-40/*.md` | 设计文档（v0.1–v0.3） |
| `_handoff/` | **接手包**：环境契约、收尾判据、第二方复核协议、技能文档 |
| `_migrate/` | 索引层生成器（六步序列） |
| `scripts/` | 门禁、凭证扫描、语料基线检查器 |
| `.githooks/` | pre-commit 守卫 |

### 工具链导航

| 子目录 | 内容 | 先看 |
|---|---|---|
| `tools/causal/` | **因果层**：图谱构建、检索、判分、提示词渲染 | `README-run.md` |
| `tools/eval/` | M1 评测：题集、gold、判分 | `eval_m1_README.md` |
| `tools/host/` | 宿主侧：隐私闸门、记忆导出 | `README-privacy.md` |

---

## ⚠️ 已知限制（拆库的固有结果）

拆库时**只带走了代码与文档**。因此：

| 限制 | 现象 |
|---|---|
| **私有层路径不可达** | 文档/脚本里引用 `02-` / `03-` / `04-` 的路径在本仓库不存在 |
| **`_migrate/` 多数脚本跑不了** | 它们遍历私有分区生成索引 ⇒ 需私有层在位 |
| **门禁部分步骤需适配** | 判据原本以私有层存在为前提（见 `scripts/git-gate.sh` 内注释） |
| **冻结指纹不完整** | `scripts/frozen-fingerprints.json` 只含公开层内可核验的条目 |
| **词表需自备** | `name_scan.py` / `desensitize_feishu.py` 的词表不在本仓库 |

> ⭐ **这些不是缺陷，是边界。** 本仓库的价值在于**方法论与实现**，
> 而不是「一个克隆即跑的完整系统」。

---

## 治理方法论（公开层值得参考的部分）

本项目在长期运维中沉淀了一套**可机械核验**的工程纪律，
它们不依赖私有语料，方法与结论都是通用的：

| 文档 | 内容 |
|---|---|
| `GIT-POLICY.md` | 分支模型、提交规范、**环境级铁律**、事故复盘 |
| `_handoff/DEFINITION-OF-DONE.md` | **收尾判据的唯一真值源** |
| `_handoff/SECOND-PARTY-REVIEW.md` | 第二方复核协议 |
| `_handoff/skills/README.md` | 技能索引（可用技能清单） |
| `PHASE-C-PLAN.md` / `REFACTOR-PLAN.md` | 巨函数拆分与重构方案（含判据与实测） |

### 几条值得单独提的方法论结论

- **校验要「能拒绝」才算校验** —— 只证明「会通过」的检查是**恒真探针**，零信息量。
- **口径未定 ⇒ 先做口径敏感性分析，再逐条判读**（同一批数据不同口径可差数十倍）。
- **「无基线率的命中率」不构成证据** —— 命中率 ≈ 基线率 ⇒ 零信息量。
- **判定维度要覆盖「形态」层**：除了「内容是否敏感」，还要问「**结构**泄露了什么」。
- **对生成物的手工修改必须回写成生成规则**，否则下次重建即丢失。

---

## 改动纪律

**一句话：改动先开分支，合进 `main` 之前先过门禁。**

```bash
# ① 开分支（分支名必须扁平，用 - 不用 /）
git update-ref refs/heads/chore-你的改动名 "$(git rev-parse main)"
git symbolic-ref HEAD refs/heads/chore-你的改动名

# ... 开发 ...

bash scripts/git-gate.sh             # ② 门禁全绿才允许合

# ③ 以 --no-ff 拓扑合入主线
TREE=$(git rev-parse 'HEAD^{tree}')
NEW=$(printf 'merge: chore-你的改动名\n' | git commit-tree "$TREE" -p main -p HEAD)
git update-ref -m "merge: chore-你的改动名" refs/heads/main "$NEW"
git symbolic-ref HEAD refs/heads/main
git branch -d chore-你的改动名
```

> ⚠️ **不要用 `git switch` / `git checkout`** —— 在项目开发环境中它们会触发
> 进程信号，而该信号会把工作区文件**批量移入回收站**（已实测两次）。
> 上面用 `symbolic-ref` + plumbing 等价实现，全程不触碰工作区。
> 完整铁律见 `GIT-POLICY.md`。

### 四条红线

1. 禁止直接向 `main` 提交 —— 有机械守卫（`git-gate.sh` + `.githooks/pre-commit`）
2. 受限素材（未脱敏真名）**永不进主线**，由独立仓库管理
3. 需 SHA256 核验的产物不得被行尾转换（本仓库 `* -text` 字节冻结）
4. 禁用 `git switch` / `git checkout` 切分支；长时 git 命令切 ≤3,000 文件/批

---

## 许可证与版权

本项目以 **GNU Affero General Public License v3.0** 发布，全文见 [`LICENSE`](LICENSE)。

AGPL-3.0 的核心要求：

- ✅ 可自由使用、修改、分发；
- ⚠️ **分发衍生作品时必须以相同许可证开源**；
- ⚠️ **通过网络提供服务（SaaS）时，也必须向用户提供源代码**（第 13 条）。

> ⚠️ **重要提示**：本仓库是**公开层**，不含语料。
> 若要复现完整评测结果，需要自备等价的私有语料与身份表 ——
> **它们不在本许可证的授权范围内**（因为不在本仓库内）。

---

_本仓库由原开发仓库按「代码公开 / 语料私有」原则拆分而成。
私有层不随本仓库发布。_