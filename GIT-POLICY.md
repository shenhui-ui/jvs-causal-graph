#-*- coding: utf-8 -*-
# ============================================================
# JVS Git 治理政策
# ------------------------------------------------------------
# 生效日期: 2026-09-16
# 适用仓库: C:\Users\<user>\Desktop\JVS (统一主仓库)
# ============================================================

## 一、一句话规则

> **改动一律先开分支，分支合并进 `main` 之前必须先过门禁；`main` 永远可回退、可克隆、可运行。**

---

## 二、仓库边界

### 已纳入本仓库

| 目录 | 说明 | 体积 |
|---|---|---|
| `01-host-product/` | 主机产品（贾维斯设计、因果工具链、主机记忆） | 工作树 13 MB / 库内 3.5 MB |
| `02-m1-evaluation/` | M1 评测与实验（D 系列、抽取产物、模型对比） | 工作树 3,691 MB / 库内 44.8 MB |
| `03-d10-workspace/` | D10 工作区（全量复核机械、语境重载、台账） | 工作树 191 MB / 库内 28.8 MB |
| `_handoff/` | 新会话接手包（环境自检、运行契约、脚本表） | 0.07 MB |
| `_index/` | 功能分类索引层（中文化分类、进度表） | 4.15 MB |
| `_migrate/` | 迁移与索引构建脚本 | 0.14 MB |
| `.workbuddy-ai/` | 工作区长期记忆与技能 | 0.04 MB |

**仓库真实占用约 78 MB**（去重 + zlib 压缩后）。

### 明确排除

| 项 | 理由 |
|---|---|
| `04-restricted-materials/` | **未脱敏真名**，D09/D10 受限件。独立仓库管理，主线永不含 |
| `**/.doubao_session.json` | 真实会话 cookie |
| `__pycache__` / `*.pyc` / `*.log` / `*.tmp` / `*.bak` / `*.orig` / `*.rej` | 可再生或临时 |
| SQLite 瞬时文件（`-journal`/`-shm`/`-wal`） | 运行期状态；`.db` 快照本身是产物，保留 |

### 为什么 `.sse` / `.zip` / 图片全部纳入

2026-09-16 实测结论（推翻了最初"排除省 96%"的判断）：

| 类别 | 工作树 | 唯一内容 | 库内占用 |
|---|---|---|---|
| `.sse` 流式原文 | 3,530 MB / 2,363 个 | 207 个 | **5.0 MB** |
| `.zip` 打包件 | 46 MB / 43 个 | 38 个 | 42.8 MB |
| 图片 | 0.6 MB / 7 个 | 7 个 | 0.6 MB |

`.sse` 工作树占 3.5 GB，但文本分片 zlib 压缩率约 1.4%、且 89.9% 为跨轮次重复，**库内只占 5 MB**。排除它却会：
1. 丢失 **5.8%** 被放弃尝试的思考链（实测 400 组中 23 组不一致，集中在 `G01-chunk02` / `G01-chunk11` / `G05-chunk05` / `G12-chunk05` / `G12-chunk13`）；
2. 使 `verify_delivery.py:110-113` 对 `.sse` 的哈希校验在克隆副本上**静默失效**（glob 空列表 → 断言跳过）；
3. 无法独立验证 `payload.response.json` 拼接正确性（实测 94.2% 逐字符一致）。

**结论：省 48 MB 不值这个代价。**

### 排除 ≠ 删除

`.gitignore` 只影响版本库内容。**本地磁盘一个字节不动**，24 个 junction、178 个脚本、既有产物全部照常运行。受影响的**只有从仓库全新 clone 出来的副本**。

---

## 三、行尾策略：字节冻结

本仓库 `.gitattributes` 使用 `* -text`，**永不做 EOL 转换**。

原因：核心资产需按 SHA256 核验，行尾归一化会改变字节、使指纹失效。

例外：`01-host-product/2026-08-28-20-59-40/.gitattributes` 保留其 `text=auto`。该子树 633 个文件的历史已按 LF 归一化入库，改用字节冻结会凭空造出 **412 个伪 modified**（实测 65.1%）。嵌套属性优先级更高，故该子树行为不变。

---

## 四、分支模型

```
main                 ← 主线，唯一受保护分支，永远可用、可克隆、可运行
 └─ dev              ← 集成分支（多特性并行时使用）
     ├─ feat-<名>    ← 新功能
     ├─ fix-<名>     ← 缺陷修复
     ├─ docs-<名>    ← 文档
     └─ exp-<名>     ← 实验（可能被丢弃）
```

> ⚠️ **本环境限制：分支名不得含斜杠。**
> 实测（2026-09-16，git 2.55.0.windows.3）：`git branch feat/xxx` 会**静默返回成功但不创建引用**，
> 连预先手动创建 `refs/heads/feat/` 目录也无效。
> 故本项目统一使用 **`类型-名称`** 扁平命名（`feat-causal-graph`、`fix-judge-retry`）。
> 斜杠命名在其他机器可用，但为跨环境一致，本仓库一律扁平。
```

### 硬性规则

1. **禁止直接向 `main` 提交。** 所有改动先开分支。
2. 分支必须从 `main`（或 `dev`）切出，命名用小写短横线，**用 `-` 而非 `/` 分层**。
3. **合并进 `main` 前必须先跑门禁**：`bash _handoff/env/check_env.sh` + `bash scripts/git-gate.sh`，全绿才允许合并。
4. 合并使用 `--no-ff`，保留分支拓扑，便于整体回退。
5. 合并后删除已并入的特性分支。

### 日常流程

> ⚠️ **本环境禁用 `git switch` / `git checkout`** —— 它们**必被 SIGTERM**，
> 而 SIGTERM 会把工作区文件**批量移入回收站**（详见第十一节铁律 1）。
> 下面全部改用 `symbolic-ref` + plumbing 等价实现，**全程不触碰工作区**。

```bash
cd /c/Users/<user>/jvs-src

# ① 开分支（等价 git switch -c，但不触碰工作区；分支名必须扁平）
git update-ref refs/heads/chore-你的改动名 "$(git rev-parse main)"
git symbolic-ref HEAD refs/heads/chore-你的改动名

# ... 开发 ...（长时 git 命令务必切 ≤3,000 文件/批，见第十一节铁律 2）

bash scripts/git-gate.sh                  # 门禁必须全绿
git add -A && git commit -m "feat(模块): 说明"

# ② 以 --no-ff 拓扑合入 main（前提：main 是当前分支的祖先）
git merge-base --is-ancestor main HEAD || { echo "非快进关系，需人工处理"; exit 1; }
TREE=$(git rev-parse 'HEAD^{tree}')
NEW=$(printf 'merge: chore-你的改动名\n' | git commit-tree "$TREE" -p main -p HEAD)
git update-ref -m "merge: chore-你的改动名" refs/heads/main "$NEW"
git symbolic-ref HEAD refs/heads/main
git branch -d chore-你的改动名
```

> 该流程产出的**两父拓扑与 `git merge --no-ff` 完全一致**，
> 已在 2026-09-19 用于 `5ca4991` / `cff291c` / `f22549b` 三次合并。

---

## 五、门禁内容（`scripts/git-gate.sh`）

实际步数**不写死**（判据：`grep -cE '^# -{10} [0-9]+\.' scripts/git-gate.sh`，2026-09-21 实测 10）。
名称取自脚本输出：

| 步骤 | 检查 | 阻塞? |
|---|---|---|
| 1 | Python 解释器可用 | 是 |
| 2 | 关键脚本语法 `py_compile`（`s4_pipeline` / `llm_judge` / `relay_call` / `targeted_round` / `name_scan`） | 是 |
| 3 | 离线回归 `test_llm_judge`（不调模型） | 是 |
| 4 | **13 项冻结产物 SHA256 与基线一致**（基线：`scripts/frozen-fingerprints.json`） | 是 |
| 5 | 核心产物未被误排除（读 `scripts/corpus-baseline.jsonl` **只增不减**路径清单 **2,406** 条 = `.sse` 2,363 + `.zip` 43，逐条 `os.stat`；缺失即 **FAIL 并指名文件**） | 是 |
| 6 | 凭证扫描（不得出现 `sk-` / 会话文件） | **是（阻塞项）** |
| 7 | 核心 JSON 可解析（`acceptance-ledger.json` / `frozen-fingerprints.json`） | 是 |
| 8 | 复核机械回归**清单**（真跑 `scripts/git-gate.sh` 内 `REG_TESTS` 列出的**全部**回归件；末行 `^OK$` 即通过，不通过仅 WARN；**清单为空 ⇒ FAIL**，反假绿） | 否（仅 WARN；清单为空除外） |
| 9 | 工作区状态（当前分支 / 无未跟踪项 / 工作区干净） | 是 |
| 10 | **技能层机械校验**（调 `scripts/skills-gate.py --quiet`：双副本对齐 / 完整性三关 / 数量断言核查；**用户级根缺失或为空 ⇒ FAIL，不设豁免开关**） | 是 |

**正常结果**（**判据，不是照抄的数字**）：

```text
FAIL=0  且  WARN=0
且  脚本自报的 OK 数  ==  输出中 [OK] 的行数
```

> ⚠️ **不要照抄 `OK` 的数值。** 它是「检查项条数」，会随步骤增删而变 —— 属「**静默过期**」高危断言
> （断言「状态」而非「事实」）。**2026-09-21 实测该值已由 `16` 变为 `15`**：第 `[5]` 步在
> `54650af`（阶段C/C-4）由「两次全树 `find`」（**2** 次 `ok()`）改为「语料基线 + `os.stat`」
> （**1** 次 `ok()`）⇒ `PASS` 恰好 −1。而本文件 §五 / `_handoff/HANDOFF.md` /
> `_handoff/NEXT-交接提醒自动化.md` / 技能 `jvs-index-layer-maintenance` **均未同步**。
>
> ⚠️ **2026-09-21 该值当天第二次变化**：新增第 `[10]` 步（**1** 次 `ok()`）⇒ `PASS` **+1**。
> ⇒ 本文件与所有引用处**一律只写判据、不写值**（现测命令见下方「自证命令」）。
> 注：`[10]` 步回显子进程输出前会**再中和一次** `[OK]`（子输出可能内嵌 README 原文，
> 而 README 是人工写的）—— 这是本条输出纪律在**新增步骤上的强制落实**，不是可选优化。
> 该守卫已用桩实测：桩故意打印 `[OK]` ⇒ 门禁回显为 `(OK)`，计数不变式仍成立。
>
> ⚠️ **第二条判据依赖一条输出纪律**：`[OK]` / `[FAIL]` / `[WARN]` 是 bash 侧
> `ok()` / `bad()` / `warn()` 的**专属标记**（各计 1 分）⇒ **内嵌 Python 片段不得打印这三个前缀**，
> 只打印缩进明细，失败用 `[缺失]` / `[不符]` 等独立 token。
> 2026-09-21 之前第 `[4]` 步违反该纪律（`print(f"  [OK]   指纹一致 N/M")` **不走 `ok()`**），
> 造成「**打印 16 行 / 计分 15**」—— 数打印行就会把基线写成 16，已修。
>
> **自证命令**（两条都要成立）：
> ```bash
> out=$(bash scripts/git-gate.sh)
> echo "$out" | grep -E '结果:'          # 记下自报的 OK 数
> echo "$out" | grep -cE '\[OK\]'        # 必须与上一条的 OK 数相等
> ```

> ⚠️ 第 8 步**曾长期是硬编码告警**：脚本里写死
> `warn "test_review_scoped_validator.py 预期 2 个 FAIL"`。
> 该告警自 `d381b38`（2026-09-16 修复该测试）起即**已失效** ——
> 实测 **31/31 通过**，但门禁仍每次报同一条 WARN。
> **永久为真的告警会训练人忽略 WARN**，属与「PENDING.md 整节被静默抹掉」同源的
> 「静默过期」问题。2026-09-19 改为**真跑测试**：通过则计 OK，不通过才 WARN。
> 因此当轮基线由 `OK=15 WARN=1` 变为 `OK=16 WARN=0`（该 `OK` 值**此后又变**，见上条 —— 勿照抄）。

> 第 4 步的基线**会过期**：产物被有意修复后若不同步基线，门禁会 FAIL。
> 同步时按第 4 节「改动纪律」走独立提交，并在 `frozen-fingerprints.json` 的
> 对应条目里加 `baseline_revision` 字段留痕（记录旧值、原因、证据）。
> 先例：2026-09-19 同步 `隐私闸门-真名扫描`（`a7e2d11f…`/4951 B → `a66fcd8b…`/6150 B，
> 因 `d381b38` 修复了 `name_scan.py` 但未更新基线）。

> 第 9 步会因**未跟踪文件**而失败 —— 新增任何文件后都要 `git add` 入库，
> 包括 `.workbuddy-ai/memory/` 下的日志。

---

## 六、提交规范

```
<类型>(<模块>): <一句话说明>
```

类型：`feat` `fix` `docs` `chore` `test` `refactor` `exp`

模块：`host` `eval` `d10` `handoff` `index` `migrate` `repo`

示例：
- `feat(d10): 新增 10 题独立留出集`
- `fix(eval): 修正 judge 重试与断点恢复`
- `docs(handoff): 补充运行目录契约`
- `chore(repo): 建立仓库治理基线`

### 禁止提交

- 凭证、cookie、会话文件
- `04-restricted-materials/` 下任何内容
- 未脱敏真实姓名
- 体积超过 50 MB 的单文件（当前无此类文件）

---

## 七、回退方式

### 撤销一次已合并的改动（保留历史，最安全）

```bash
git log --oneline                    # 找到目标提交
git revert <commit-sha>              # 生成反向提交
git commit -m "revert: 说明"
```

### 整条分支作废（未合并）

```bash
git branch -D feat/废弃分支
```

### 回退到某个里程碑（丢弃其后所有改动）

```bash
git tag                                  # 查看所有里程碑
git symbolic-ref HEAD refs/heads/main    # 勿用 git checkout main（见第十一节铁律 1）
git reset --hard <标签名>                 # 谨慎：丢弃工作区改动
```

### 里程碑标签（2026-09-19 逐个核验：以下 4 个**真实存在**）

| 标签 | 指向 | 含义 |
|---|---|---|
| `jvs-migration-20260916` | `0c13016` | 项目迁入 JVS + 24 junction 建立 |
| `jvs-governance-20260916` | `0c13016` | git 治理基线建立（本政策生效） |
| `jvs-recovery-20260916` | `dbe4327` | 2026-09-16 事故恢复后的权威基线 |
| `d10-holdout-20260916` | `0c13016` | D10 十题留出集交付 |

> ⚠️ 本节曾列出的 `m1-baseline-v0.6` 与 `d10-review-r19` **并不存在**
> （2026-09-19 用 `git rev-parse -q --verify refs/tags/<t>` 逐个核验），
> 照抄会让 `git reset --hard` 直接报错。**补出真标签之前，不要把它们写回文档。**

### 极端情况：本地目录被破坏

迁移记录见 `MIGRATION-RECORD.md`。junction 可随时重建：

```bash
# 以 01 为例（仅重建链接，不动数据）
rmdir "C:\Users\<user>\host-workspace\2026-08-28-20-59-40"
mklink /J "C:\Users\<user>\host-workspace\2026-08-28-20-59-40" "C:\Users\<user>\Desktop\JVS\01-host-product\2026-08-28-20-59-40"
```

---

## 八、为什么不用 submodule

`01-host-product/2026-08-28-20-59-40` 原有独立 `.git`（4 次提交）。本项目选择**并入统一仓库**而非作为 submodule，理由：

1. **单仓库可整体回退** —— submodule 的版本漂移会让"回到某个时间点"变成两处操作；
2. **一次 clone 拿到全部** —— 含 03 工作区的复核机械与产物；
3. **避免 embedded repository 告警** —— 内嵌 `.git` 会被 git 记成 gitlink，导致 `git add -A` 静默跳过整个子树。

并入方式：`git subtree`（只读导入，保留 4 次提交的原始 hash 与作者信息）。

**内嵌 `.git` 已于 2026-09-18 移除。** 移除前做了字节级安全验证：

| 验证项 | 结果 |
|---|---|
| 4 个提交对象是否在主仓库 | **全部存在**（`77a669c` / `c8139e5` / `d9c1e8c` / `c6c87e9`） |
| subtree merge 结构 | `4eb02df` 的第二个父提交即 `c6c87e9` |
| 该 merge 的 01 子树 tree | **精确等于**内嵌 HEAD tree `180d37cc…` |
| 内嵌独有文件路径 | **0 个** |
| 同名文件内容差异 | 415 个 —— 均为**行尾差异**（内嵌为 `text=auto` 归一化后的 LF，主仓库为「回归原始字节」后的 CRLF），另有个别真实更新（如 `name_scan.py`） |

> 即：内嵌 `.git` 保存的是**已被 `5ca6904` 修复掉的坏状态**，其全部对象在主仓库中可达，删除**零损失**。
> 原 `.git`（76 文件 / 2.9 MB）已移至工作区之外备份：
> `C:\Users\<user>\_staging\embedded-git-backup-20260918\.git`

---

## 九、受限素材独立仓库

`04-restricted-materials/` 单独 `git init`，`.gitignore` 与主线独立：

```bash
cd /c/Users/<user>/jvs-src/04-restricted-materials
git init
git add -A
git commit -m "chore(restricted): 建立受限素材独立仓库基线"
```

**该仓库永不配置远端、永不外发。**

---

## 十、变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-16 | 建立治理基线；确定方案 A（无损全纳入）；`* -text` 字节冻结；01 历史子树并入；受限素材独立仓库 |
| 2026-09-16 | 分支命名改为扁平式（实测本环境含斜杠会静默失败）；补充 `.sse`/`.zip` 无损纳入的实测依据 |
| 2026-09-16 | **新增禁忌：严禁中断 `git merge` / `git gc` / `git checkout`**（事故复盘见下） |
| 2026-09-18 | **移除 01 子树内嵌孤儿 `.git`**（字节级验证后执行，见第八节）；清理 3 处无关空目录 `NVIDIA Corporation/umdlogs`；清理 26 个 `__pycache__` / 193 个 `*.pyc` |
| 2026-09-19 | **新增环境级铁律（见第十一节）**：`git switch` / `git checkout` 在本环境**必被 SIGTERM**，且 SIGTERM 会把工作区文件**批量移入回收站**；长时 git 命令必须切成 ≤3,000 文件的有界批次 |
| 2026-09-19 | **合并改用 plumbing 等价实现**（`commit-tree` + `update-ref` + `symbolic-ref`），全程不触碰工作区；生成的两父拓扑与 `git merge --no-ff` 完全一致。`chore-org-cleanup` 已并入 `main` 并删除，合并提交 `5ca4991` |
| 2026-09-19 | **门禁第 8 步由硬编码告警改为真跑测试**。原写死 `warn "test_review_scoped_validator.py 预期 2 个 FAIL"`，该告警自 `d381b38` 修复后即失效（实测 31/31 通过），属「静默过期」。基线由 `OK=15 WARN=1` 变为 **`OK=16 WARN=0`** |
| 2026-09-19 | 索引层刷新（21,115 → 21,143）与 `_handoff/PENDING.md` 生成器覆盖手写整节的修复，见 `ORGANIZATION-AUDIT.md` §八 |
| 2026-09-21 | **新增第十三节「linked worktree 与分支模型的冲突」**（宿主会自行创建 worktree 并签出非 main 分支；`+` 前缀即「被 worktree 检出」；`symbolic-ref` 不检查占用 ⇒ 曾把提交直落 main 并生成单父提交）。同轮**清理**宿主创建的那个 worktree（3.9 GB / 21,137 文件）与已并入的 `fix-semantic-emb` 分支：先证「39 个疑似独有文件的内容逐字节存在于 `main` 可达历史」，留 55 MB bundle 保险，删后核验主仓 HEAD / 跟踪数 / 脏文件均未变 |
| 2026-09-21 | **门禁基线的 `OK` 值由「写死数字」改为「判据」表述**。起因：第 `[5]` 步在 `54650af`（阶段C/C-4）由 2 次 `ok()` 改为 1 次，`OK` 由 `16` **静默变为** `15`，而 §五 / `_handoff/HANDOFF.md` / `_handoff/NEXT-交接提醒自动化.md` / 技能 `jvs-index-layer-maintenance` 均未同步。同轮修掉第 `[4]` 步的**输出纪律违反**（内嵌 Python 直接 `print "[OK] 指纹一致 N/M"`、不走 `ok()` ⇒ 打印 16 行 / 计分 15）⇒ 现**打印 `[OK]` 行数 == 自报 `OK` 数**。另更正一处独立错误：`check_env.sh` 的 2 个 WARN 是**环境固有**（`RELAY_KEY`/`PROBE_KEY` 未设置、`openclaw` 需 node ≥22.22.3），提交后仍为 `WARN=2`、**不可能归零**，原「提交后应得 `WARN=0`」系把两个门禁混为一谈 |
| 2026-09-21 | **门禁第 8 步由「硬编码单文件」改为「清单驱动」**（用户授权，C-2 前置）。`scripts/git-gate.sh` 内新增 `REG_TESTS` 列表，纳入 C-2 新补的两件回归件（`test_review_full_validator` / `outputs/test_verify_delivery`）；判据与旧版**逐字一致**（末行 `^OK$`），「非阻塞」语义不变。⚠️ **新增反假绿守卫**：清单为空 ⇒ `FAIL` —— 否则清单一旦被清空，该步会**静默等于没跑**。⚠️ 同时记下一个**写法陷阱**：**不得**改用 `echo "$LIST" \| while read ...`，管道会让循环跑在**子 shell**，`ok`/`warn` 递增的是子 shell 的计数器 ⇒ 汇总行与「自报数 == `[OK]` 行数」双双失真（已用负向自检实测证实该写法计数为 0）。⚠️ 该轮 `OK` 由 `16` 变 `17`：`[8]` 由 1 个 OK 变 3 个（**+2**）、`[9]` 因提交前树脏少 1（**−1**）⇒ **净 +1，已逐行 diff 归因，不是回归** |

### 事故复盘：中断 merge 导致 `.git` 受损（2026-09-16）

**经过**：合并 `docs-git-memory` 时 `git merge --no-ff` 长时间无输出，被当作卡死强制终止。
之后 `.git` 无法识别：**`refs/` 目录整体消失**，`objects/pack/` 只剩 `.idx`，
配套的 51.87 MB `.pack` **被移动到回收站**。

**损失**：4 个提交的对象丢失（`c9a7f46` 终检脚本、`e52bd81a` 分支命名文档、
`a07a1d8` merge 提交、`cdd98c5` 记忆/待办/技能）。
工作树 21,380 个文件**未受影响**，13 项冻结产物指纹 **13/13 一致**。

**恢复**：

1. 保全现场 —— 复制受损 `.git` 关键件到工作区之外。
2. 全盘搜索 pack，确认主仓库 `.pack` 已不在磁盘。
3. **在回收站命中** `.pack`（`$I` 元数据记录的原始路径指向 `.git/objects/pack/`）。
4. **原生校验 pack**（不依赖 git）：`magic=PACK version=2 objects=6722`；
   尾部 20B sha1 == 对前 `len-20` 字节求得的 sha1 == 文件名中的 `141d9b5d…`；
   `.idx` 对象数 6722、尺寸自洽 `28N + 1072 = 189288`、记录的 pack sha1 相同；
   **6722 个对象全部可完整解压，末尾剩余 0 字节 —— 无截断**。
5. **依 reflog 重建 refs**：`.git/logs/` 逐分支记录了最终指向；
   `.git/info/refs` 保留了 tag object SHA（含 `packed-refs` 缺失的 `jvs-governance-20260916`）。
6. 确定 `main` 复位到 pack 原生存在的 `0c13016`，其后提交的内容从工作树重建。
7. `git read-tree HEAD` 重建索引（不动磁盘一个字节）。

**教训**：

1. **不要中断 `git merge` / `git gc` / `git checkout`。**
   本环境未见输出不等于卡死；应放入后台运行并观察。
2. 恢复顺序必须是：**先保全 `.git` 现场 → 再找 pack（含回收站）→ 再依 reflog 重建 refs**。
   **refs 丢了不可怕，`logs/` 与 `info/refs` 才是权威。**
3. pack 的 `.idx` 与 `.pack` 必须成套；只有 `.idx` 时该 pack 不可用，
   但可由 `.idx` 的对象数反推完整性（尺寸自洽 `28N + 1072`）。
4. **工作区内的改动可能被环境回滚**（本环境存在修改前备份/回滚机制），
   重要恢复件与结论文档应放在**工作区之外**。

### 事故复盘：SIGTERM 导致工作区被批量移入回收站（2026-09-18 / 09-19）

**经过**：两次事故，同一根因。

| 次 | 时间 | 触发的命令 | 工作区被回收 |
|---|---|---|---|
| 1 | 2026-09-18 21:49–21:57 | `git switch main`（后台任务，7m43s 后被终止） | **9,765 个**（其中跟踪文件 9,551） |
| 2 | 2026-09-19 10:04 | `git switch main`（前台，数秒内被终止） | **6,447 个** |

**关键证据（证明不是 git 干的）**：

1. `.git/logs/HEAD` 中**没有** `switch main` 的 reflog 条目 —— 两次 `git switch` 都在写 reflog 之前就被杀掉了；
2. `main` 与 HEAD 的指向**始终未变**，无 `MERGE_HEAD`，合并从未发生；
3. git 删除工作区文件走 `unlink`，**不会进回收站**；而两次事故的文件都是**被回收**的；
4. 连 `.git/index.lock`、`.git/HEAD.lock`、`.git/packed-refs.lock`、`.git/AUTO_MERGE.lock`
   **本身也被回收了** —— git 绝不会这样处理自己的锁；
5. 回收速率呈机器批量特征：2026-09-18 稳定在 **~1,280 文件/分钟**，与后台任务窗口完全重合。

**结论**：本环境对**长时 / 阻塞型 git 命令会发 SIGTERM**，而 SIGTERM 会触发宿主机制
把工作区文件**批量移入回收站**。不是 git 的行为，也不是磁盘故障。

**损失**：**零**。`git status` 全程只有 ` D` 一种状态，无任何 ` M` ——
现存文件内容与 HEAD 一致，未被回滚成旧版本。

**恢复**（两步都已在实战中验证）：

```bash
# 1) 清掉 SIGTERM 留下的陈旧锁（0 字节）
rm -f .git/index.lock

# 2) 按清单有界批次还原（每批 ≤3,000 个文件）
git ls-files --deleted -z > remain.nul     # 切成若干块
git restore --source=HEAD --worktree \
  --pathspec-from-file=<chunk.nul> --pathspec-file-nul
```

**校验**：9,551 / 9,551 个文件的 `git hash-object` 结果与其 HEAD blob sha1 **逐字节一致**，0 不符。
门禁复跑 `OK=15 WARN=1 FAIL=0`，`.sse` 2363/2363、`.zip` 43/43、冻结指纹 13/13。

**回收站交叉验证**（第一次事故）：9,830 个条目中，仓库内路径 9,816 个；
与缺失清单命中 **9,500 / 9,551**；全量大小比对 9,500 一致 / 0 不符；抽样 SHA256 30/30 一致。
剩余 51 个（`.workbuddy-ai` 5 个 + D10 evidence 46 个）不在回收站，但**全部在 git 对象库中**。

> **`$I` 元数据解析坑**：偏移 24 的 `plen` 是 **UTF-16 字符数，不是字节数**。
> 路径 = `d[28 : 28 + plen*2]`。按字节解析会得到被截断的假路径，导致误报「回收站 0 命中」。

**教训**：

1. **本环境 `git switch` / `git checkout` 不可用。** 改用 plumbing（见第十一节铁律 1）。
2. **任何 git 操作都要切成有界批次。** 实测单次命令安全上限约 9,000 个文件；
   生产上按 **≤3,000 个文件/命令** 切。
3. **SIGTERM 后必查 `.git/index.lock`**，它是 0 字节残留，会阻塞后续所有 git 写操作。
4. 数据从未真正丢失 —— **git 对象库是最后一道防线**。事故后第一件事是
   `git cat-file --batch-all-objects --batch-check` 确认对象库完好，而不是去动工作区。

---

## 十一、环境级铁律：SIGTERM 与有界批次

> 本节是**操作层硬约束**，与其它各节的流程要求并列。违反即可能触发第十节的事故复盘。

### 铁律 1：禁用 `git switch` / `git checkout` 切换分支

本环境对这两个命令**必发 SIGTERM**（2026-09-18、09-19 各复现一次），
且 SIGTERM 会导致工作区文件被批量移入回收站。

| 想做的事 | 不要用 | 改用 |
|---|---|---|
| 切分支 | `git switch` / `git checkout` | `git symbolic-ref HEAD refs/heads/<branch>`（只改 `.git/HEAD`） |
| 在 main 上生成合并提交 | `git switch main && git merge --no-ff <br>` | `git commit-tree` + `git update-ref` |
| 还原工作区文件 | `git checkout -- .` | `git restore --source=HEAD --worktree --pathspec-from-file=…` |

合并提交的 plumbing 等价实现（前提：`main` 是目标分支的祖先，此时合并结果树 = 目标分支树）：

```bash
TREE=$(git rev-parse <branch>^{tree})
NEW=$(printf '%s\n' "$MSG" | git commit-tree "$TREE" -p main -p <branch>)
git update-ref refs/heads/main "$NEW"
git symbolic-ref HEAD refs/heads/main      # 只重写 .git/HEAD（34 字节），不动工作区与索引
git branch -d <branch>
```

> `git symbolic-ref` 只重写 `.git/HEAD`，**不触碰工作区与索引**，是安全的。
> 注意：`git update-ref` 默认**不写 reflog 说明**，事后需补：
> `git update-ref -m "<说明>" refs/heads/main <sha> <sha>`。

### 铁律 2：长时 git 命令必须切成有界批次

实测单次命令的安全上限约 **9,000 个文件**，超出即被 SIGTERM。
生产口径：**≤3,000 个文件/命令**。

```bash
# 生成 NUL 分隔清单（免疫空格与特殊字符）
git ls-files --deleted -z > remain.nul
# 切块后逐块还原
git restore --source=HEAD --worktree \
  --pathspec-from-file=chunk1.nul --pathspec-file-nul
```

**不要用 `git restore -- .` 全仓一把梭** —— 这正是 2026-09-19 被 SIGTERM 的那条命令。

### 铁律 3：SIGTERM 之后必查三件事

1. `ls .git/index.lock` —— 0 字节残留锁，必须 `rm -f`；
2. `git ls-files --deleted | wc -l` —— 确认缺失规模；
3. `git cat-file --batch-all-objects --batch-check | grep -c missing` —— 确认对象库完好。

### 铁律 4：先信对象库，再信回收站

对象库完好时**一律走 `git restore`**，不要动回收站：

- 对象库覆盖**全部**跟踪文件（含回收站里没有的 51 个）；
- 内容由 blob sha1 保证，可逐字节复算校验；
- 回收站只有 9,500/9,551，且需逐个重建目录结构。

回收站的价值在于**未跟踪文件**（缓存、生成物），以及对象库也受损时的最后手段。

### 铁律 5：`wmic` 不可用；进程检查用 `tasklist` + 重试

本环境 `wmic: command not found`。检查残留 git 进程用
`tasklist | grep -i "git\.exe"`，并**加 3–5 次重试** ——
自己的上一条命令可能还没退干净，会误判成「有 git 进程」而中止。

### 铁律 6：红线 1 有**机械守卫**（2026-09-23 新增，两道）

「禁止直接向 `main` 提交」以前**只是纸面纪律** —— 没有任何机械拦截。
2026-09-23 实测证实该缺口**真实存在**：一次常规 `git commit` 直提 `main`，
产生**单父**提交 `20ce9fe`（父 = `5b811b8`），违反红线 1 与
`_handoff/DEFINITION-OF-DONE.md` §6 第三条（`main` 末提交父数须为 **3**）。

现补两道守卫，互为补充：

| # | 位置 | 判据 | 时点 | 可否绕过 |
|---|---|---|---|---|
| ① | `.githooks/pre-commit`（`[0]` 段） | `git symbolic-ref -q HEAD` == `refs/heads/main` ⇒ **拒绝提交** | **每次提交**（git 强制调用） | 仅 `--no-verify` 或 `JVS_ALLOW_COMMIT_MAIN=1` |
| ② | `scripts/git-gate.sh`（`[9]` 段） | 处于 `main` 且**末提交父数 `< 3`** ⇒ **FAIL** | 合入前 / 在 main 上收尾时 | 不可（本步不被跳过） |

```bash
# 守卫 ① 的应急开口（须显式知情；会打印警告但仍放行）
JVS_ALLOW_COMMIT_MAIN=1 git commit -m "..."
```

**判据设计取舍**（有意如此，勿"改进"）：

- 判据取 `< 3` 而**非** `!= 3`：同时覆盖「单父（=2）」与「无父（=1，初始提交）」。
- 守卫 ① **不检测 detached HEAD** —— 那种形态在本仓属异常但**并非红线 1**，
  且 `git rebase` / `cherry-pick` 会临时处于 detached ⇒ 一律拦会误伤。
- 守卫 ① **不检测 linked worktree** —— 该风险是「合并退化成单父」，
  已由守卫 ② 的父数判据覆盖（更准）。
- 守卫 ① **零依赖**：只用 `git symbolic-ref`，不调 Python（即使 `JVS_PY` 不可用也生效）。

**负向自证**（本项目纪律：**「校验要能拒绝才算校验」**，只证明"会通过"= 恒真探针）：

判据须逐类篡改输入自证。已跑 6 个场景，**6/6 通过**（夹具在临时仓库内，
零触碰真仓库的 `main` 与工作区）：

| # | 场景 | 期望 |
|---|---|---|
| ① | 在 `main` 上提交 | 拒绝（rc=1）且**指名红线 1** |
| ② | 在功能分支提交 | 放行（rc=0），输出**不含**红线 1 |
| ③ | 逃生开关 `JVS_ALLOW_COMMIT_MAIN=1` | 放行但**必须含警告** |
| ④ | detached HEAD | **不因**红线 1 而拒 |
| ⑤ | `af66cc1`（修复后双父） | 父数 3 ⇒ 判 `ok`（不误伤） |
| ⑥ | `20ce9fe`（直提单父） | 父数 2 ⇒ 判 `bad`（**能拒绝**） |

> ⭐ ⑤⑥ 用的夹具是**本次事故的真实提交**，不是合成数据 —— 比合成夹具强度更高。
> 夹具脚本：`_staging/negcheck-redline1.py.sh`（工作区外）。

**本次事故的修复方式**（保留历史，可追溯；`main` 回退 + `--no-ff` 重建）：

```bash
BASE=$(git rev-parse main^)          # 直提前的 main（本身是双父合并）
V=$(git rev-parse main)              # 违规的单父提交
git update-ref refs/heads/tmp-keep "$V"                    # ① 先锚定，保住对象
git update-ref refs/heads/main "$BASE" "$V"                # ② CAS 回退 main
TV=$(git rev-parse "$V^{tree}")
NEW=$(printf 'merge: <branch>\n' | git commit-tree "$TV" -p "$BASE" -p "$V")  # ③ 双父
git update-ref refs/heads/main "$NEW" "$BASE"
git merge-base --is-ancestor "$V" main && git update-ref -d refs/heads/tmp-keep
# 复核：git rev-parse main^{tree} 必须 == $TV（内容零变化）
```

> ⚠️ **回退 `main` 属 CAS 语义**：`git update-ref <ref> <new> <old>` 的第三个参数是
> **期望的当前值**，不匹配即拒绝 —— 这是防误动的关键，**不要用 `-f` 绕过**。

---

_本政策与 `_handoff/HANDOFF.md` 配套使用：本文件管「怎么动 git」，HANDOFF 管「项目到哪一步了」。_

## 十二、`.gitignore` 误伤与「未入库」陷阱（2026-09-20 新增）

### 案例

`.gitignore` 的 `env/` 规则（本意忽略 Python venv）匹配**任意层级**的 `env` 目录，
把接手包 `_handoff/env/` 一并排除 ⇒ `ENV.md` / `RUN-CONTRACT.md` / `check_env.sh`
**从未入库**（`git check-ignore -v` 明示规则出处为 `.gitignore:42`）。

### 为什么长期没被发现

生成器用 `os.path.isfile()` 判断存在性。文件在**本机主工作树**里确实存在
⇒ 生成物显示 ✅；而**任何克隆或新工作树都拿不到** ⇒ 接手第一条命令
`bash _handoff/env/check_env.sh` 必然失败。

属「静默过期」家族：**断言本机状态，而非版本库事实**。

### 判据（判断「文件是否可用」一律查版本库，不查磁盘）

```bash
git ls-files --error-unmatch <path>   # 未被跟踪则报错
git check-ignore -v <path>            # 查是否被忽略，并打印规则出处
git status --short --ignored=matching -- <path>
```

### 通用教训

`.gitignore` 里**不带前导 `/` 的目录名**（`env/`、`build/`、`dist/`、`node_modules/`）
会匹配任意层级。仓库里若有同名业务目录，必须加白名单例外 `!<path>/`，
或改用根锚定 `/env/`。

### 已修

- `.gitignore` 第 43 行加 `!_handoff/env/`（实测 `git check-ignore` rc=1，不再被忽略）；
- 三文件入库（commit `22a66a0`）；
- `make_scripts_md.py` 判据改为 `mark()`：✅ 已入库 / ⚠️未入库 / ❌ 不存在。

## 十三、linked worktree 与分支模型的冲突（2026-09-21 新增）

> 可执行步骤见技能 **`git-sigterm-safe-recovery`**（含「清理宿主创建的 linked worktree」一节）。

### 背景：宿主会自行创建 worktree 并让它签出非 main 分支

WorkBuddy 宿主会在 `<WorkBuddy>/Worktrees/<项目>/<名>` 下**自动创建** linked worktree。
2026-09-20 实测：该 worktree 的 HEAD 曾指向**含斜杠**的 `refs/heads/workbuddy/main-faa1418b`
（连带触发本政策 **§四** 的扁平命名事故）；2026-09-21 检查时它签出的是扁平分支
`fix-semantic-emb`，占用 **3.9 GB / 21,137 文件**。

### 与分支模型的冲突（两条，均须记住）

1. **`git branch -vv` 的 `+` 前缀 = 「被某个 worktree 检出」** ——
   **看到 `+` 就不能说「遗留分支」「可安全删除」**；必须先 `git worktree list` 查清。
   （⚠️ 本环境实测过一次误判：把被 worktree 占用的分支当成遗留引用。）
2. **`git symbolic-ref HEAD` 不检查「该分支是否已被别的工作树检出」** ——
   曾导致 worktree 与主工作树**同时检出 main**，随后的 `git commit` **直接落到 main**、
   绕过合并流程；连做 `--no-ff` 时的 `-p` 参数也解析成同一提交 ⇒ 报
   `error: duplicate parent ... ignored`、生成**单父提交**。

### 因此新增的收尾核验（并入既有合并流程）

```bash
git worktree list                      # 每次都查：谁检出了什么
git symbolic-ref --short HEAD          # 提交前必验，不要假设
git rev-list --parents -n 1 main       # 合并后必须是 3 个 sha（双父）
```

### 清理 worktree 的纪律（2026-09-21 实测）

- **先证「零独有内容」再去删**：磁盘 vs 跟踪 → worktree 跟踪 vs 主仓 HEAD →
  **这 39 个疑似独有的内容是否逐字节存在于 `main` 可达历史**。
  ⭐ **判据必须查到「历史可达性」这一层** —— 「main HEAD 树里没有」**不等于**「数据会丢」。
- **删前必留保险**（置于工作区外）：`git bundle create <f>.bundle refs/heads/<分支>`
  （含 complete history）+ blob sha 表 + 分支 sha。
- 执行：`git worktree remove <路径>` → **核验主仓完好**（HEAD / 脏文件 / `ls-files` 计数未变）
  → `git update-ref -d refs/heads/<分支>`。⚠️ `worktree remove` **递归删文件，属高危**。
- ⭐ **宿主要维持空目录骨架**：删后残留的空目录会被宿主**数秒内重建** ⇒ **不要对抗**。
  **判「清理是否成功」看 `du -sh <Worktrees>/`（=0）与 `git worktree list`，不看目录是否存在** ——
  **删掉的是内容，留下的是骨架**（0 字节）。
