# 完成定义（DEFINITION-OF-DONE）—— 收尾的单一真值源

> 建立 **2026-09-21**（对齐方案 P2；对应上游机制 **M10「共享清单由多技能引用、不复制」**）。
>
> **本文是「一个任务何时算完成」的唯一权威源。** 技能里的「收尾 / 收尾清单 / 交付前校验 /
> 收尾必做」小节**只写本技能专属项**，通用项一律**引用本文**，不再各自重复（写法见 §11）。
>
> **路径基准**：本文相对路径均相对 JVS 仓库根 `C:\Users\<user>\Desktop\JVS`。
>
> **判据基准**：git 层的规则细节**不在本文重复**，见 `GIT-POLICY.md`
> （§四 分支模型 / §五 门禁内容 / §十一 环境级铁律 / §十二 未入库判据 / §十三 linked worktree 收尾核验）。

## 0. 一句话

**「完成」= §1–§9 全绿**（按任务类型裁剪，见 §10；涉及**跨时间对照判分产物**时 **§12** 亦为必做）。
任何一步不绿 ⇒ **未完成** ——
不得宣告完成、不得交接、不得删分支。

## 1. 编辑存活核验（⭐ 必须排在跑生成器之前）

本环境会**按行回滚**仓内编辑（同一文件一处被还原、另两处保留）⇒ 改完**立即回读**。

```bash
grep -cF '<关键串>' <file>      # 输出必须全是预期的 1
```

- ⚠️ **核验必须早于跑生成器**：生成器输入含文件尺寸 ⇒ 晚核验要整轮重跑。
- ⚠️ 命中 0 时**先怀疑自己的写法**（转义、`strip` vs `rstrip`、全角/半角、`--` 开头被当选项、
  markdown 行内加粗），再怀疑被测对象。比对**人写的文档**前先剥掉 `*` `_` `` ` `` 并归一化空白。
- 纪律：**「一条判据若在语义正确时仍可能报错，那它是坏判据，不是严格判据。」**

## 2. 索引层刷新（六步 + 集合差）

产物落在 `_index/`、`_handoff/`、`03-…` 等**会被索引**的路径时必做。

```bash
git add -A                    # ★ 必须先 add 再跑（生成物含「是否入库」标记）
python _migrate/classify.py
python _migrate/make_progress_md.py
python _migrate/make_scripts_ledger.py
python _migrate/make_scripts_md.py
python _migrate/classify.py   # 顺序不可换：最后一次 classify 必须排在产物生成器之后
python _migrate/make_index_md.py
git add -A
```

**集合差判据**（必须用 **Python**；`comm` 排序口径不同会假报）：

- **消失 = 0**（旧索引有、新索引无）
- `total == 子类求和`（如 `项目专用 + 通用`）
- 陈旧条目 = 0

⚠️ **`_index/一次性脚本登记表.md` 内嵌生成时间戳**（`生成时间：YYYY-MM-DD HH:MM`）
⇒ 每次重跑**必然**产生 1 行 diff，**不是漂移**，不必追查。
判「索引是否**真的**变了」只看 `_index/文件索引.jsonl` 里各条目的 `p` / `s` 字段差
（实测 2026-09-21：重跑后仅 `_handoff\DEFINITION-OF-DONE.md` 的 `s` 从 7382 → 7773）。

⚠️ 若任务涉及**受限素材或环境备份**，`git add` 改用**显式路径**而非 `-A`（避免误纳）。
细节见技能 **`jvs-index-layer-maintenance`**。

## 3. 双门禁（两个都要跑）

```bash
bash scripts/git-gate.sh                # 放后台
bash _handoff/env/check_env.sh          # 放后台
```

| 门禁 | 判据 |
|---|---|
| `git-gate` | `FAIL=0` **且** `WARN=0` **且** 自报 `OK` 数 == `grep -cE '\[OK\]'` 行数 |
| `check_env` | **只判 `FAIL=0`** —— 它的 `WARN=2` 是**环境固有**（`RELAY_KEY` / `PROBE_KEY` 未设置等），**提交后仍为 2、不可能归零** |

- ⚠️ **提交前** `git-gate` 的 `WARN` 是「未跟踪新文件 + 未提交变更」= 待提交内容本身，**提交后须归零**。
- ⚠️ **勿照抄数字**：`OK` / `WARN` / `FAIL` 的具体值一律**现跑现读**；判据是**关系**，不是数值。
- 完整真值源 = `GIT-POLICY.md` §五 + 技能 `jvs-index-layer-maintenance`。

## 4. 提交与合并（禁用 `git switch` / `git checkout`）

本环境对 `git switch` / `git checkout` **必发 SIGTERM**，而 SIGTERM 会把工作区文件
**批量移入回收站** ⇒ 一律走 plumbing。

```bash
git update-ref refs/heads/<b> <base>            # 建分支（两行缺一不可）
git symbolic-ref HEAD refs/heads/<b>
# ... 编辑 + git add + git commit -F <工作区外的提交信息文件> ...
git commit-tree "$TREE" -p main -p <b> -F <msg> # 合并
git update-ref refs/heads/main "$NEW" "$OLD"
git symbolic-ref HEAD refs/heads/main           # ★ 删分支前必须先切回 main
git update-ref -d refs/heads/<b>
```

- **分支名必须扁平**（禁斜杠）：含 `/` 会让 `git branch` / `git rev-parse` **静默失效**。
- 单条 git 命令处理 **≤3,000 文件**；**绝不中断** `git merge` / `gc` / `checkout`（长时无输出 ≠ 卡死）。
- 提交长信息**不用 heredoc**。
- ⚠️ **路径一律写 Windows 风格（`C:/Users/...`）**：Git Bash 里 `/c/Users/...` 会被
  **Windows 原生 git** 当成相对路径 ⇒ `fatal: could not read log file ...`
  （实测 2026-09-21）。凡是**接文件路径**的 git 参数 —— `-F` / `--file` / `--output` ——
  都用 `C:/...`；只有**传给 bash 自己**的变量（如 `$STAGE`）才用 `/c/...`。
- ⚠️ worktree 与主工作树**不得同时检出同一分支**（否则 `git commit` 直提 `main`，
  合并报 `duplicate parent ... ignored`、生成**单父提交**）⇒ 见 `GIT-POLICY.md` §十三。

细节见技能 **`git-sigterm-safe-recovery`**。

## 5. 提交后回读（读 HEAD，**不读工作区**）

```bash
git show HEAD:<path> | grep -F "<串>"
git ls-tree -r --name-only HEAD -- <新目录> | wc -l   # 必须 > 0
```

- ⚠️ **工作区对 ≠ 库里对**：提交信息声称的改动**可能根本不在库里**（新建目录漏 `git add`）。
- ⇒ 复核一律 `git show HEAD:<path>`，**不要** `cat <path>`。

## 6. 工作区收尾三条

> ⚠️ **第 0 条（提交前必做，2026-10-06 新增）**：**看 `git status --porcelain` 的完整列表**，
> 逐行确认每一条都是本轮预期改动。
> **为什么**：`git add -A` 会**静默**把意外产生的文件一并暂存 —— 实测一次事故：
> 用 `python -c "…"` 内联一段**含 `>` 的字符串**时，bash 把 `>` 当**重定向**，
> 于是在**仓库根目录造出 35 个垃圾文件**（名字形如 `→` / `⇒` / `⚠️` / `⭐` / `做 独立复核…`），
> `git add -A` 全部纳入暂存；六步也因此报「未归类 **36**」（正常为 1）。
> ⇒ **两条纪律**：① **绝不用 `python -c "…"` 内联含 `>` / 反引号 / `|` 的文本** ——
> 一律**先 Write 成 `.py` 文件再执行**；② 提交前**看列表**（本轮正是靠 porcelain 里那串诡异名字当场发现）。
> 处置办法（若已发生）：按 `git diff --cached --diff-filter=A` 取新增项，**DRY-RUN 打印**后再删，
> 删完 `git add -A`（从未提交的文件删盘即出索引）并**重跑六步**（六步已按污染态算过索引）。

```bash
git ls-files --deleted | wc -l            # → 0
git status --porcelain | wc -l            # → 0
git rev-list --parents -n 1 main | wc -w  # → 3（双父提交；非 3 说明合并没生成双父）
```

> ⭐ 第三条**已有机械守卫**（2026-09-23 新增，两道）：
> `git-gate.sh` 的 `[9]` 段（处于 `main` 且末提交父数 `< 3` ⇒ **FAIL**）与
> `.githooks/pre-commit` 的 `[0]` 段（HEAD 是 `refs/heads/main` ⇒ **拒绝提交**）。
> 判据、设计取舍与负向自证见 **`GIT-POLICY.md` 第十一节 铁律 6**。
> 根因：2026-09-23 实测发生过一次直提 `main`（单父提交 `20ce9fe`），
> 证明该红线此前**只是纸面纪律**。

## 7. 判「文件是否可用」查版本库，不查磁盘

`os.path.isfile` / `ls` 都会漏掉「**本机存在但未入库**」的文件。

```bash
git ls-files --error-unmatch <path>     # 未被跟踪则报错
git check-ignore -v <path>              # 查是否被忽略，并打印规则出处
```

跨工作树同步后索引与 HEAD 可能不对齐（`MM` / `D ` / `??` 混杂）⇒ 用 `git read-tree HEAD`
重置索引（**只动索引、不碰磁盘**，比 `git reset` 更不容易误伤）。

细节见 `GIT-POLICY.md` §十二。

## 8. 双副本同步（改了技能才有这一步）

技能有两份**字节一致**副本：

| 副本 | 路径 | 被 git 跟踪 |
|---|---|---|
| 用户级（权威） | `C:\Users\<user>\.workbuddy-ai\skills\<n>\` | 否 |
| 项目级 | `_handoff/skills/{项目专用,通用}/<n>\` | **是** |

⇒ 改完必须 `cp` 同步，并 `diff -r` 验字节一致（**无输出**）。否则项目级副本**静默过期**。

### ⚠️ 第三处路径（`.workbuddy-ai/skills/`）**不是副本，是指针**（2026-09-25 新增）

`.workbuddy-ai/skills/` 是**宿主的项目级技能约定路径**，与上表的项目级副本**不是同一套**。
历史上这里放过 4 份技能，实测 **2 份已与权威副本不一致** ⇒ 形成**第二真值源**。

⇒ 纪律：**该目录只允许存在一份指针 `README.md`，不得放任何 `*/SKILL.md`**。
由 `scripts/skills-gate.py` 的 **`[6]` 段**机械守卫（fail-closed：缺目录 / 缺指针 README /
出现技能副本 ⇒ 一律阻塞），负向自证见 `python scripts/skills-gate.py --self-test`。

## 9. 留痕（所有任务必做）

- 追加项目记忆 `C:\Users\<user>\Desktop\JVS\.workbuddy-ai\memory\YYYY-MM-DD.md`
  （**append-only**，不覆盖）。
- 交付物用 `present_files` 展示。

## 10. 按任务类型裁剪

| 任务类型 | 必做 | 可跳过 |
|---|---|---|
| 只读勘察（不改仓库） | §9 | §1–§8 |
| 只改文档、无生成器产物 | §1 §4 §5 §6 §9 | §2（§3 视仓库策略） |
| 改技能 | §1 §2 §3 §4 §5 §6 §8 §9 | — |
| 改受限素材 | 额外走独立本地库流程 | 见技能 `jvs-restricted-review-backfill` |
| ⭐ **跨时间对照判分产物**（新旧 `scores` / 指标读数） | **§12 先行且必须 PASS**，再按上表裁剪 | — |

## 11. 技能里的写法约定（防止再造真值源）

技能收尾小节统一写成：

```markdown
## 收尾

通用收尾序列见 `_handoff/DEFINITION-OF-DONE.md`（相对 JVS 仓库根）。本技能专属项：

- <只写本技能独有的判据>
```

- **禁止**在技能里重复 §1–§9 的通用项 —— **本文是唯一真值源**。
- 技能专属项**必须保留**：那些只有该技能才适用的判据（如 `validate` 输出字段、
  受限库内先提交、逐字节还原、A/B 判据等）。

## 12. 判分侧漂移防护（红线 25 制度化，2026-09-29）

> **本节的落点是「纪律有明确落点」，不是「只写在红线表里」。** 对应 P1-3。

### 12.1 铁律

**凡要跨时间对照由 LLM 判分派生的任何指标（`cause_hit` / `causal_accuracy` /
`availability` / `judge_success_rate` …），必须先跑 `judge_drift_guard.py` 并取得 `PASS`。**

未取得 `PASS` ⇒ **新旧 `scores.json` 之差不得作为效应量**，只能作为「待核验」登记。

### 12.2 判据（自证件 + 四种结论）

```bash
PY="C:/Users/<user>/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe"
G="01-host-product/2026-08-28-20-59-40/tools/causal/judge_drift_guard.py"

# ① 离线（零 LLM，首选）：重放录制响应
"$PY" -B "$G" --baseline 旧scores.json --replay responses.jsonl --out report.json
# ② 对比两份判分产物；--qids 显式声明同轴题集
"$PY" -B "$G" --baseline 旧scores.json --replay-scores 新scores.json --qids <qid,...>
# ③ 在线（消耗配额）：走真实入口重判同一批输入
"$PY" -B "$G" --baseline 旧scores.json --answers a.jsonl --gold g.jsonl --live
```

| 结论 | 含义 | 退出码 | 允许的后续动作 |
|---|---|---|---|
| `PASS` | 同输入、判分有效、0 翻转 | 0 | 可作同轴对照 |
| `DRIFT` | 同输入但出现翻转 | 1 | **停**：登记漂移，不得出效应量 |
| `REFUSE` | 题集不完备 / 空交集 / 重复 qid / 判分失败 / 字段非法 | 1 | **停**：先补齐可比性 |
| `NOT_SAME_INPUT` | 两侧 `input_fingerprint` 不一致 | 1 | **停**：这不是同输入重判 |

- ⚠️ **`REFUSE` 不得被读成「无漂移」** —— 空交集/缺题时「0 翻转」是假阴性。
- ⚠️ **判分失败 ≠ 判定为假**（红线 23）：任一侧 `judge_ok≠true` 一律 `REFUSE`，不得拿占位 `false` 去比。
- ⚠️ 任一侧缺 `input_fingerprint` ⇒ 报告标 `same_input_verified=false`，**不静默放过**。

### 12.3 机械守卫与证据

- `scripts/git-gate.sh` **[3] 段**跑 `judge_drift_guard.py --self-test`（13 类夹具，阻塞）。
- 夹具覆盖四方向（REFUSE 7 / DRIFT 3 / NOT_SAME_INPUT 1 / PASS 2）⇒ **能拒绝**且**不是恒拒**。
- 真实已裁决样本标定：`_staging/p13-calibration-20260929/` ——
  同 `input_fingerprint` 的 09-18/09-23 对 ⇒ `DRIFT`；不同输入的两次 D10 运行 ⇒ `NOT_SAME_INPUT`；自比 ⇒ `PASS`。
