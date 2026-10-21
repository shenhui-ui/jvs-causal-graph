---
name: concurrent-session-merge-arbitration
description: 当同一个 git 仓库有多个会话/进程/自动任务并发提交时使用 —— 包括「合并后别人的改动不见了」「并发会话的提交被回退」「main 被推进后自己合不上」「提交里混进别人的文件」「git add 之后暂存区多出没碰过的文件」「多个会话同时合并谁先谁赢」「按优先级而不是按时间合并」「生成物/索引文件冲突怎么处理」。也适用于设计或审计这类仲裁脚本。
agent_created: true
---

# 多会话并发提交的仲裁与安全合并

## 这个技能解决什么

当**多个会话共用同一个 git 仓库**（人工 + AI 助手 + 定时任务），
「各自建分支 → 合入 main」这套单人流程会暴露出**两类会静默丢数据的失效模式**，
以及一个流程缺口：**两个会话同时要合并时，没有任何规则决定谁先、谁赢**。

本技能给出：两个坑的根因与正解、一条安全的提交/合并操作序列、以及按优先级仲裁的设计要点。

---

## 一、坑 1：stale-tree 合并（**会静默回退别人的改动**）

### 症状

- 合并完之后，**另一个会话刚提交的改动不见了**（`git log` 里能查到那个提交，但内容没了）；
- 或者 `git diff --stat <并发末态> main` 显示**一堆删除**，其中有些是你根本没碰过的文件；
- 更迷惑的情形：你事后回看，**diff 却是空的** —— 因为并发方自己又做了一次三方合并把内容救回去了。

### 根因

很多文档教的合并写法是：

```bash
TREE=$(git rev-parse "$BRANCH^{tree}")            # ← 取分支自己的树
NEW=$(git commit-tree "$TREE" -p main -p "$BRANCH")
```

**它有一个隐藏前提：「main 是目标分支的祖先」。** 此时合并结果树恰好等于分支树。
一旦 main 被**并发推进**，`$BRANCH^{tree}` **不含** main 上的新改动 ⇒
合并提交把那些改动**整体回退**，且**不报错**。

### 正解（无条件正确，天然适配并发）

```bash
# merge-tree 做真正的三方合并，返回合并结果树；不读写工作区 / index / HEAD
TREE=$(git merge-tree --write-tree main "$BRANCH") || { echo "有冲突，需仲裁或人工"; exit 1; }
NEW=$(printf '%s\n' "$MSG" | git commit-tree "$TREE" -p main -p "$BRANCH")
git update-ref -m "merge: <branch>" refs/heads/main "$NEW" "$(git rev-parse main)"   # CAS
```

要点：

- `git merge-tree --write-tree`（git ≥ 2.38）**不需要** main 是祖先，
  官方明示「does not read from or write to either the working tree or index」。
- ⚠️ **不要**用 `git merge-base --is-ancestor` 当**前置门**：并发场景下它**必然失败**，
  而失败**不等于**「不能合并」，只说明「需要三方合并」。
- ⚠️ **真冲突时 `merge-tree` 的 rc=1**，但**首行仍是（带冲突标记的）树 sha，且在 stdout**；
  冲突清单也在 stdout（配 `--name-only -z` 可脚本解析），**stderr 为空** —— 别去 stderr 找。
- ⚠️ `-X ours` / `-X theirs` 会**静默消除冲突**（rc 从 1 变 0）⇒
  **必须先跑一次不带 `-X` 的 `merge-tree` 取冲突清单并留证**，再决定是否消解。

### 硬判据（提交后立刻查）

```bash
git diff --stat "$(git rev-parse <新提交>^)" <新提交>      # 不得出现你没做过的删除
```

---

## 二、坑 2：共享 index 污染（**提交里混进别人的文件**）

### 症状

- `git diff --cached --name-only | wc -l` **大于**你 `git add` 的数量；
- 提交 diff 里出现你**没碰过**的路径。

### 根因

`git add` 写的是**全仓唯一的** `.git/index`。两个会话同时操作时，
甲的 `git write-tree` 会把**乙已 `git add` 但尚未提交的文件**一并快照进甲的提交。

### 正解：临时索引（本批提交用一份私有索引）

```bash
export GIT_INDEX_FILE=/<工作区外>/_tmp_idx
rm -f "$GIT_INDEX_FILE"
git read-tree HEAD            # ← 播种基线！见下方警告
git add -- <只 add 本批文件>
T=$(git write-tree)           # 只含本批
C=$(git commit-tree "$T" -p "$(git rev-parse main)" -F msg.txt)
unset GIT_INDEX_FILE; rm -f "$GIT_INDEX_FILE" "$GIT_INDEX_FILE.lock"
```

> 🔴 **播种基线必须与 `commit-tree -p` 的父是同一时刻的 main。**
> 若先 `read-tree HEAD` 播种、之后 main 被并发推进、再用 `-p main` 提交 ⇒
> **重现坑 1**（树基于旧 main，缺并发改动）。
> 实测症状：提交 diff 里出现别的会话文件的**大段删除**。
> **修法**：提交前用「**当时**的 main」重新 `read-tree` 播种。
> **判据**：`git diff --stat <parent> <新树>` **不得出现删除**。

> ⚠️ 这条路同时解决「**并发会话抢走 `.git/HEAD`**」：`commit-tree` / `write-tree`
> / `update-ref` **都不读也不写 HEAD** ⇒ 全程 plumbing，免疫 HEAD 漂移。

> ⚠️ **`git mktree` 不接受含 `/` 的路径**（实测 `fatal: path a/b.json contains slash`）。
> 要「按路径替换树里的条目」，用临时索引 + `git update-index --cacheinfo` + `write-tree`。

---

## 三、并发环境的安全操作序列（速查）

```bash
# 0. 隔离：全程用私有索引，不碰共享 .git/index
export GIT_INDEX_FILE=/<工作区外>/_tmp_idx; rm -f "$GIT_INDEX_FILE"

# 1. 锚定 base（记下，后面每一步都要复核它没变）
BASE=$(git rev-parse main)

# 2. 播种（用当时的 main；若 main 已变，回到第 1 步）
git read-tree "$BASE"
git add -- <本批文件>
git diff --cached --name-only | wc -l        # 必须 == 你 add 的数量

# 3. 写提交（不碰 HEAD）
T=$(git write-tree)
C=$(git commit-tree "$T" -p "$BASE" -F msg.txt)
git diff --stat "$BASE" "$C"                 # 不得出现删除

# 4. 合并（三方 + CAS）
MT=$(git merge-tree --write-tree main "$C") || echo "有冲突"
M=$(git commit-tree "$MT" -p main -p "$C" -m "merge: ...")
git update-ref refs/heads/main "$M" "$(git rev-parse main)"   # 第三个参数=CAS 期望旧值
```

> ⚠️ **CAS 的第三个参数不要用 `-f` 绕过** —— 它挡住「把并发会话刚推的 main 覆盖掉」。
> ⚠️ **`update-ref` 默认不写 reflog 说明**，需要时补一条
> `git update-ref -m "<说明>" refs/heads/main <sha> <sha>`。

---

## 四、设计优先级仲裁（当两个会话同时要合并）

### 业界现状（先知道自己在哪）

主流方案（GitHub merge queue / bors-ng / Zuul / Mergify）**全是「队列排序 + 冲突即拒绝 / 重试 / 人工」**，
**没有**「冲突胜者由自定义优先级决定」的现成实现。
⇒ 仲裁**策略层**要自研；**底层零件是 git 原生的**（`merge-tree -X ours` + 临时索引 + CAS `update-ref`）。

### 设计要点

| 要点 | 做法 |
|---|---|
| **优先级来源** | 放**仓库外的配置文件**（真值源），**不要用提交信息** —— 见下 |
| **身份载体** | 提交信息 **trailer** 可承载身份（`git interpret-trailers --parse --no-divider`），但**只承载身份，不承载权力** |
| **胜负规则** | 数字大者胜；冲突时高优先级覆盖低优先级 |
| **「能拒绝」** | 低优先级触碰高优先级/既有内容 ⇒ **拒绝并转人工**，而不是静默 `-X ours` 通过 |
| **生成物豁免** | 索引/台账类**生成物**的冲突**不仲裁**，保留 ours 并列入「待重生成」⇒ 合并后重跑生成器 |
| **不推进夹带** | 有**任一被拒**候选 ⇒ **整体不推进 main** |
| **CAS 收口** | 最后一步永远用 `update-ref ... <new> <expected-old>` |

### 三条反直觉但必须遵守的

1. **trailer 不防伪**：提交信息是纯文本，会话自填即可。
   ⇒ **凡「必须被强制」的优先级，必须来自仓库外配置**；trailer 里写的优先级**一律忽略**。
2. **`-X ours` 会丢「发生过冲突」这一事实**：所以「**先探测、再消解、留证据**」是两步，不是一步。
3. **生成物不能仲裁**：对生成物做人工/自动裁决 = 制造「生成物与生成器分叉」的真错。
   **正确姿势是合并后重跑生成器**。

---

## 五、抗理性化

| 你可能想说 | 事实 |
|---|---|
| 「我先 `--is-ancestor` 查一下，不是祖先就人工处理」 | 并发下它**必然**不是祖先，你会**每次都走人工**，等于没自动化；正解是直接 `merge-tree` |
| 「我 `git add` 完看了一眼没别人的文件，应该没事」 | 「看一眼」不是判据；判据是 `git diff --cached --name-only \| wc -l` **== 你 add 的数量** |
| 「提交完我自己复查过了，内容都在」 | **工作区对 ≠ 库里对**；一律 `git show <sha>:<path>` 回读 |
| 「有冲突就让 `-X ours` 过吧，省事」 | `-X ours` **静默消冲突**；先不带 `-X` 探测留证，再决定，否则「冲突过」不可追溯 |
| 「优先级写在 commit message 里最方便」 | 那样**谁都能自抬身价**；权力必须在仓库外配置 |
| 「生成物冲突也一起仲裁了吧」 | 生成物由生成器产出；仲裁它 = 制造分叉错，下一轮生成器跑完还会翻回来 |
| 「我 `--selfcheck` 全绿了」 | ⭐ **只证明「会通过」= 恒真探针**。必须**故意把正确写法改坏**再跑，若结果**不变**，说明判据没用 |

---

## 六、红旗（出现即停手查因）

- 提交 diff 里出现**你没碰过**的路径；
- `git diff --stat <parent> <新树>` 出现**你没做过的删除**；
- `git diff --cached --name-only | wc -l` **> 你 add 的数量**；
- 合并后 `git log` 能查到某提交、但**内容不在**；
- `git rev-parse main` 与几分钟前**不同**（有并发在推）；
- `git rev-parse --abbrev-ref HEAD` **前后两次不一致**；
- `merge-tree` 返回 rc=0 但你认为**应该有冲突**（可能被 `-X` 静默消解了）。

---

## 七、验证（怎么证明做对了）

```bash
# ① 提交后：内容回读（查版本库，不查磁盘）
git show <sha>:<path> | grep -F "<关键串>"
# ② 提交后：无意外删除
git diff --stat "$(git rev-parse <sha>^)" <sha>
# ③ 合并后：双父 + 工作区三条
git rev-list --parents -n 1 main | wc -w      # 3
git ls-files --deleted | wc -l                # 0
git status --porcelain | wc -l                # 你已知的并发改动数，且有解释
# ④ 仲裁器：负向自证（红线：判据必须能拒绝）
python <arbitrator> --selfcheck               # 逐类篡改输入，应全部被拒
# ⑤ ⭐ 二次自证：把正确写法故意改坏，重跑 —— 结果必须变红
```

> **④⑤ 缺一不可**：④ 只证明「设计上会拒绝」，⑤ 才证明「判据真的连在代码上」。

---

## 八、边界（勿过度外推）

- **不自动解语义冲突**：优先级只能决定「谁覆盖谁」，**不能判断内容对错**。
  低优先级触碰高优先级内容**一律拒绝、转人工**（涉及人工签字/语义裁决的场合，
  自动化不得代签、不得作独立语义裁决）。
- **不替代门禁 / 评审**：仲裁器**不跑**测试与门禁；被合入内容仍须由提交方自证全绿。
- **数值分配是治理动作**：优先级即权力分配，属项目方裁定范畴 ⇒
  工具可提议、**不得擅自改既有参与者优先级**。
- `merge-tree --merge-base` 与 `--stdin` 不兼容；显式给 `--merge-base` 时官方提示
  结果「可能与 `git merge` 不同，可能丢改动」⇒ **别拿它替代自动选基**。

---

## 九、收尾清单

- [ ] 合并用的是 `merge-tree --write-tree`（**不是** `<branch>^{tree}`）
- [ ] 提交前用**当时的 main** 播种临时索引
- [ ] 提交 diff 里**无意外删除**、暂存条目数**== 自己 add 的数**
- [ ] `update-ref` 带 **CAS**（第三个参数），未用 `-f`
- [ ] 生成物冲突走**豁免 + 待重生成**，不是仲裁
- [ ] 仲裁器跑过 `--selfcheck`，且做过**「改坏后应变红」**的二次自证
- [ ] 合并后回读**版本库**（`git show main:<path>`），不是读工作区