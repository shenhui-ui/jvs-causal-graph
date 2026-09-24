---
name: git-sigterm-safe-recovery
description: 在「长时 git 命令会被 SIGTERM、且 SIGTERM 会把工作区文件批量移入回收站」的环境里安全操作 git —— 切分支、做 --no-ff 合并、以及把被回收的文件从 git 对象库逐字节还原。当出现「git switch 卡住/被杀」「大批文件在 git status 里显示 D」「工作区文件莫名消失」「文件进了回收站」「合并完不成」时使用。
agent_created: true
---

# git SIGTERM 安全操作与批量还原

## 这个技能解决什么

某些宿主环境（已实测：WorkBuddy 桌面端 Windows + PortableGit 2.55）会对
**长时 / 阻塞型 git 命令发 SIGTERM**，而 **SIGTERM 会触发宿主机制把工作区文件批量移入回收站**。

典型症状：

- `git switch <branch>` / `git checkout <branch>` **永远完不成**，几秒或几分钟后被杀；
- 之后 `git status` 出现**成千上万个 ` D`**（索引有、HEAD 有、工作区没有）；
- `git status` **只有 ` D`、没有 ` M`** —— 这是关键特征，说明没被回滚成旧版本；
- 目录壳还在但内部文件空了；
- `.git/index.lock` 留下 0 字节残留锁。

**先记住结论：数据几乎不会真丢。** git 对象库是最后一道防线，工作区文件都能逐字节还原。

---

## 第 0 步：立刻判断「是不是真丢」

```bash
cd <repo>
# 1) 对象库是否完好（最重要的一步，先做这个）
git cat-file --batch-all-objects --batch-check | awk '{print $2}' | sort | uniq -c
git cat-file --batch-all-objects --batch-check | grep -ci "missing\|broken"   # 期望 0

# 2) 索引与 HEAD 树是否对齐
git ls-files | wc -l
git ls-tree -r HEAD | wc -l        # 期望两者相等

# 3) 缺失规模与状态分布
git ls-files --deleted | wc -l
git status --porcelain | awk '{print $1}' | sort | uniq -c   # 期望只有 D
```

**判据**：对象库 0 missing 且 `git ls-files == git ls-tree -r HEAD` → **全部可还原**，
不要慌张、不要动回收站、更不要 `git reset --hard`。

> 若出现 ` M`（修改）而不是 ` D`，说明是**环境回滚**（另一种机制），
> 处理方式不同：用 `git restore --source=HEAD --worktree -- <paths>` 还原内容。

---

## 第 1 步：清掉残留锁

SIGTERM 必然留下 `.git/index.lock`（0 字节），它会阻塞后续**所有** git 写操作。

```bash
# 先确认没有残留 git 进程（注意：wmic 在本环境不可用）
for i in 1 2 3 4 5; do
  tasklist 2>/dev/null | grep -qi "git\.exe" && { echo "等待 3s ($i)"; sleep 3; } || break
done
tasklist 2>/dev/null | grep -qi "git\.exe" && { echo "仍有 git 进程，中止"; exit 1; }
rm -f .git/index.lock
```

> **必须加重试**：自己的上一条命令可能还没退干净，会误判成「有 git 进程」而中止。

---

## 第 2 步：切分支 / 做合并 —— 永远不要用 `git switch`

### 切分支

```bash
# ① 先看清谁检出了什么（尤其「+」前缀 = 被某个 worktree 检出）
git worktree list
git branch -vv

# ② 从 base 建分支并切过去 —— 必须两行都执行
git update-ref refs/heads/<branch> <base-sha>      # 创建分支引用（不可省！）
git symbolic-ref HEAD refs/heads/<branch>          # 只重写 .git/HEAD（34 字节），不动工作区与索引
```

> ⚠️ **`git symbolic-ref HEAD` 只改 `.git/HEAD` 这一个 34 字节文件，它不会创建分支引用。**
> 若分支名**尚不存在**且你**没先 `update-ref`**，`HEAD` 就指向一个**未出生分支**（unborn branch）：
> `git rev-parse HEAD` / `git rev-list --count HEAD` 全部失败。
> ⭐ **2026-09-21 实测踩到**：凡**实时**读 `HEAD` 的脚本都会**静默回落成占位值** ——
> `make_progress_md.py` 的「生成时 HEAD」一行直接写成 `` `?`，提交数 ? ``，
> **既不报错也不中断生成**。（该生成器自己声明「失败则回落为占位，不让生成器整体失败」，
> 所以要靠**肉眼比对基线**才能发现。）
> **判据**：切完分支后立刻 `git rev-parse --short HEAD` **必须返回 sha**，返回错误即为未出生分支。

> ⚠️ **`git symbolic-ref HEAD` 是底层命令，不检查「该分支是否已被别的工作树检出」。**
> 2026-09-20 实测：本仓有 linked worktree（`C:/Users/<user>/WorkBuddy/Worktrees/JVS/main-faa1418b`），
> 执行 `git symbolic-ref HEAD refs/heads/main` 后**worktree 与主工作树同时检出 main**，
> 紧接着的 `git commit` **直接提交到了 main**，绕过了合并流程。
>
> 连带后果：做 `--no-ff` 时 `-p main -p "$BR"` 里的 `$BR`（取自 `symbolic-ref --short HEAD`）
> **也解析成 main** ⇒ git 报 `error: duplicate parent ... ignored`，
> 生成的是**单父提交**，而不是合并提交。
>
> **三条铁律**：
> 1. **提交前必须确认当前分支**：`git symbolic-ref --short HEAD` —— 不要假设。
> 2. **worktree 与主工作树不要同时检出同一分支** —— worktree 侧一律用**独立扁平分支**
>    （`fix-xxx`），`main` 只由主工作树检出；先 `git worktree list` 看清谁检出了什么。
> 3. 合并提交生成后**核验是双父**：`git rev-list --parents -n 1 main` 应输出 **3 个** sha。
>
> 见到 `duplicate parent ... ignored` ⇒ 立刻意识到「两个 `-p` 指向了同一提交」，
> 说明分支解析错了，**不要继续往下做**。
>
> **现状（2026-09-21）**：该 worktree 与 `fix-semantic-emb` 分支**已清理**
> （详见下节「清理宿主创建的 linked worktree」）。上述三条铁律**仍然有效** ——
> 宿主**可能再次创建** worktree，`git worktree list` 必须每次都查。

### 清理宿主创建的 linked worktree（2026-09-21 实测）

宿主（WorkBuddy）会在 `C:/Users/<user>/WorkBuddy/Worktrees/<项目>/<名>` 下**自动创建**
linked worktree，并让它签出一个**非 main 的分支**。

⭐ **第 0 步（最容易漏，也最贵）：先看 `git branch -vv` 的 `+` 前缀。**

```
  fix-semantic-emb 42b3b32 (C:/Users/<user>/WorkBuddy/Worktrees/JVS/main-faa1418b) ...
+ fix-semantic-emb 42b3b32 (C:/Users/<user>/WorkBuddy/Worktrees/JVS/main-faa1418b) ...
                    ^ 这个 `+` = 「被某个 worktree 检出」
```

⚠️ **2026-09-21 实测踩过**：我只看了 `git branch -a`，没看 `-vv`，
就把它当成「已并入 main 的**遗留引用**」并说「删除安全」——
实际它被一个 **3.9 GB / 21,137 文件**的 worktree 签出着。
⇒ **看到 `+` 就不能说「遗留」「可安全删除」**；必须 `git worktree list` 查清谁检出了什么。

🟥 **2026-09-22 第三次踩（同族，且这次是「高级命令绕过保护」）** ——
本技能明写着「看到 `+` 就不能删」，我**确实看到了 `+`**、也确实跑了 `git worktree list`
（输出里该分支正被 `Worktrees/JVS/main-702c5075` 检出），**却仍下了删除动作**。
根因：把它类推成上一条已清理的案例，认为「已被 main 包含 ⇒ 安全」。

⇒ ⭐ **新技术点（必记）**：**`git update-ref -d refs/heads/<b>` 会绕过 `git branch -d` 的「分支被工作树检出」保护**，
**静默删除成功**，而对应 worktree 的 HEAD **立刻悬空**（`git worktree list` 显示 `0000000`）。

| 命令 | 被 worktree 检出时 | worktree 后果 |
|---|---|---|
| `git branch -d <b>` | ❌ **拒绝**（`Cannot delete branch ... checked out at ...`） | 安全 |
| `git update-ref -d refs/heads/<b>` | ✅ **静默成功**（无任何警告） | ⚠️ **HEAD 悬空（`0000000`）** |

**恢复**（对象未丢，只是 ref 没了；`f3ae7d4` 仍在对象库）：
```bash
git cat-file -t <sha>                                   # 先确认对象完好
git update-ref refs/heads/<b> <sha>                     # 重建 ref
git worktree list                                       # 复核：不再显示 0000000
```
**本次实测恢复成功**（`f3ae7d4` 复原，无数据丢失）。

⭐ **判据（把技能已有原则具体化）**：**删任何 ref 之前，必须先用 `git worktree list`
逐条核对「该 ref 是否出现在任一工作树的方括号里」** ——
**不能只看「是否已被 main 包含」**（技能第 131 行原话：这是两件独立的事）。
⇒ **凡用 plumbing（`update-ref -d`）代替 porcelain（`branch -d`），必须自己补做被跳过的那道检查。**

⚠️ 另注意：**「已并入 main / 是 main 祖先」与「被 worktree 占用」是两件独立的事** ——
前者只说明**内容不丢**，不说明**没有工作树在用它**。

清理前**必须**按序证明「零独有内容」：

```bash
W="<worktree 路径>"
# ① 磁盘 vs 跟踪（差集应只剩 .git 与 __pycache__/*.pyc）
find "$W" -type f -not -path "$W/.git/*" | sed "s|^$W/||" | sort > /tmp/disk.txt
(cd "$W" && git ls-files | sort) > /tmp/tr.txt
comm -23 /tmp/disk.txt /tmp/tr.txt
# ② worktree 跟踪文件 vs 主仓 HEAD（差集是「疑似独有」清单，必须逐个查）
comm -23 /tmp/tr.txt <(git ls-tree -r --name-only HEAD | sort)
# ③ 对每个「疑似独有」：内容是否逐字节存在于 main 可达历史？
#    命中 ⇒ 删了也不丢数据；未命中 ⇒ 必须先导出
H=$(git hash-object "$W/$f")
for c in $(git rev-list main --max-count=200); do [ "$(git rev-parse "$c:$f")" = "$H" ] && break; done
```

**保险（删除前必做）**：`git bundle create <工作区外>/<name>.bundle refs/heads/<分支>`
（含 complete history，最坏情况可整支取回）+ 导出 blob sha 表 + 记分支 sha。

**执行**：`git worktree remove "<路径>"`（会递归删该目录）→ 核验**主仓完好**
（`git rev-parse HEAD` 未变 / 脏文件 0 / `git ls-files | wc -l` 未变）→
`git update-ref -d refs/heads/<分支>` → 再核验 `git branch -a` 只剩 `main`。

⚠️ **`git worktree remove` 递归删文件，属高危** —— 先备份 bundle，删后立刻核验主仓跟踪数。
⚠️ 分支删除**只删引用**；其提交因是 `main` 祖先而**仍可达**（`git cat-file -t` 仍返回）。

⭐ **宿主要维持空目录骨架 —— 别去删它**（2026-09-21 实测）：
`git worktree remove` 后目录树只余空目录（**0 字节**），我尝试 `rmdir` 收尾，
`<名>` 与 `<项目>` 两级**删除成功但数秒内被原样重建**（mtime 变为删除那一刻），
`Worktrees` 则始终 `Directory not empty` ⇒ **宿主在监视并维持该路径骨架**。

⇒ **判「清理是否成功」看这两条，不看目录是否存在**：

```bash
du -sh "<WorkBuddy>/Worktrees/"    # 0（原 3.9 GB ⇒ 内容已释放）
git worktree list                  # 只剩主工作树
git branch -a                      # 只剩 main
```

**删掉的是内容，留下的是骨架** —— 收益已达成，**不要与宿主反复对抗**。

### 建分支并提交

```bash
git update-ref refs/heads/<new-branch> "$(git rev-parse HEAD)"
git symbolic-ref HEAD refs/heads/<new-branch>
git add <files> && git commit -m "..."
```

### ⚠️ 分支名必须扁平（禁斜杠）—— 否则提交看似成功而引用丢失

2026-09-20 实测，**后果比 `GIT-POLICY.md` R13 记录的更严重**：

- HEAD 指向 `refs/heads/workbuddy/main-faa1418b`（**含 `/`**）；
- `git commit` **报告成功**并打印新提交号（`c0aa8bf`），对象也确实写进了对象库；
- 但 `refs/heads/workbuddy/` **目录根本没被创建** ⇒ 紧接着
  `git rev-parse HEAD` 报 `does not have any commits yet`、`git log` 为空、
  `git show HEAD:<path>` 报 `invalid object name 'HEAD'`。

⇒ **`git commit` 成功 ≠ 引用存在。** 建分支一律用扁平名（`fix-xxx`），
提交后必须验这三条：

```bash
git rev-parse HEAD                     # 能解析出 sha
git log --oneline -1 HEAD              # 能列出来
git show HEAD:<path> | grep -F "<串>"   # 内容真的在库里
```

**引用丢了不必重做工作**：提交对象仍在对象库时，`git cat-file -t <sha>` 确认后
用 `git update-ref refs/heads/<flat-name> <sha>` 重新挂上即可。
**保险**：改动完成即导出 `git format-patch -1 <sha> --stdout > <工作区外>.patch`。

### ⚠️ 提交后必须回读 HEAD，不能回读工作区

**工作区对 ≠ 库里对。** 2026-09-19 实测事故：一次提交的说明声称
「技能 11 份 + 3 份副本已同步」，但库里其实是**中间态** ——
`git show HEAD:<file>` 仍是「共 8 份」，3 个新建技能目录 `git ls-tree -r HEAD` 返回 **0 文件**
（从未 `git add`）。最终修正**只存在于工作区**，直到合并后逐项验证才暴露。

```bash
# 改了已跟踪文件 → 比内容
git show HEAD:<path> | grep -F "<关键串>"              # 用 -F 固定串，别用正则
# 新建了目录 → 比清单（最容易漏）
git ls-tree -r --name-only HEAD -- <new-dir> | wc -l   # 必须 > 0
# 提交前先看暂存区是否真的含新目录
git diff --cached --name-only
```

- **新建目录极易漏 `git add`** —— 「改文件 + 新建目录 + 提交」连续做时，
  收尾一定要 `git ls-tree -r HEAD -- <新目录>` 确认**不是 0 个文件**。
- 复核一律用 `git show HEAD:<path>`，**不要读工作区文件** ——
  工作区可能已被环境回滚，也可能装着尚未提交的改动，两种都会给出错误结论。
- **检查串必须从文件里抄**：先 `grep -o` / `sed -n` 打印真实行，再照抄成检查串。
  凭印象写会得到**假阴性**（报 FAIL 而文本其实正确），2026-09-19 一轮内连中三次。

### 生成 `--no-ff` 合并提交（前提：main 是目标分支的祖先）

祖先关系成立时，**合并结果树 = 目标分支树**，所以完全不需要触碰工作区：

```bash
git merge-base --is-ancestor main <branch> || { echo "非快进关系，此方案不适用"; exit 1; }
TREE=$(git rev-parse <branch>^{tree})
NEW=$(printf '%s\n' "$MSG" | git commit-tree "$TREE" -p main -p <branch>)
git update-ref -m "merge: 合入 <branch>" refs/heads/main "$NEW"
git symbolic-ref HEAD refs/heads/main
git branch -d <branch>
```

> `git update-ref` **默认不写 reflog 说明**，事后补一条 no-op 记录：
> `git update-ref -m "<说明>" refs/heads/main <sha> <sha>`

**校验**（应全部通过）：

```bash
git rev-list --parents -n1 HEAD        # 应有两个父提交
git diff --stat HEAD <branch>          # 应为空
git status --porcelain | wc -l         # 应为 0
git log --oneline --graph -4           # 应看到 |\ 与 |/ 拓扑
```

分支名**必须扁平**（用 `-` 不用 `/`）—— 某些环境 `git branch feat/x` 会静默成功但不创建引用。

---

## 第 3 步：把被回收的文件从对象库还原（有界批次）

**单次命令约 9,000 个文件即触顶被 SIGTERM。生产口径 ≤3,000 个文件/命令。**

```bash
# 生成 NUL 分隔清单（免疫空格与特殊字符）
git ls-files --deleted -z > /path/outside/repo/remain.nul
```

切块（Python 示例，写到**工作区之外**）：

```python
import subprocess, os
REPO = r"C:\path\to\repo"; OUT = r"C:\path\outside\repo"; CHUNK = 3000
r = subprocess.run(["git","-C",REPO,"ls-files","--deleted","-z"], capture_output=True)
paths = [p for p in r.stdout.split(b"\x00") if p]
for i in range(0, len(paths), CHUNK):
    with open(os.path.join(OUT, "chunk%d.nul" % (i//CHUNK+1)), "wb") as f:
        f.write(b"\x00".join(paths[i:i+CHUNK]) + b"\x00")
```

逐块还原（**每块一条命令**，不要合并成一条）：

```bash
git restore --source=HEAD --worktree \
  --pathspec-from-file=/path/outside/repo/chunk1.nul --pathspec-file-nul
git ls-files --deleted | wc -l        # 复核：应单调下降
```

**不要** `git restore -- .` / `git checkout -- .` 全仓一把梭 —— 这正是会触发 SIGTERM 的写法。

---

## 第 4 步：逐字节校验

```python
# 对每个还原的路径：git ls-tree -r HEAD 给出应有 blob sha1；
# git hash-object 独立重算磁盘内容 sha1；逐条比对。
# 注意：仅当 .gitattributes 无行尾转换（如 `* -text`）时二者才等价。
import subprocess
REPO = r"C:\path\to\repo"
want = {}
for item in subprocess.run(["git","-C",REPO,"-c","core.quotePath=false",
                            "ls-tree","-r","-z","HEAD"],capture_output=True).stdout.split(b"\x00"):
    if item:
        meta, p = item.split(b"\t",1)
        want[p.decode("utf-8","surrogateescape")] = meta.split(b" ")[2].decode()
paths = [l.rstrip("\n") for l in open("restored-paths.txt", encoding="utf-8") if l.strip()]
got = subprocess.run(["git","-C",REPO,"hash-object","--stdin-paths"],
                     input=("\n".join(paths)+"\n").encode(), capture_output=True).stdout.decode().split()
bad = [(p,e,g) for p,e,g in zip(paths,(want.get(p) for p in paths),got) if e != g]
print("一致 %d / 不符 %d" % (len(paths)-len(bad), len(bad)))
```

---

## 回收站：什么时候用、怎么用

**默认不用。** 对象库覆盖全部跟踪文件；回收站只覆盖大部分，且需逐个重建目录结构。

回收站的价值：
1. **未跟踪文件**（缓存、生成物、环境自己的会话备份）—— 对象库里没有；
2. 对象库也受损时的最后手段。

`C:\$Recycle.Bin\<SID>\` 可直接读（`Shell.Application` COM 在受限环境会被安全策略拦截）。

> **要把文件「放进」回收站**（而不是读取）时：可用通道与一个**致命坑**见技能
> `capability-recon-before-build` 的「真要删东西时」节 ——
> `[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory(...)` **成功也会抛异常**
> （「无法找到指定文件」/「这个系统不支持该功能」），**异常不是判据**；
> 唯一可靠判据 = 数回收站的 `$I` 元数据 / `$R` 载荷，且进站后文件**改名成 `$R<随机6位>`**
> ⇒ **按原文件名搜不到 ≠ 没进回收站**（本人一度据此误判）。

**`$I` 元数据格式（关键坑）**：

```
偏移 0-7   : header（低字节为版本号 1 或 2）
偏移 8-15  : 文件大小 (uint64 LE)
偏移 16-23 : 删除时间 FILETIME (uint64 LE)
偏移 24-27 : 路径长度 plen (uint32 LE)  ← 是 UTF-16 字符数，不是字节数！
偏移 28+   : UTF-16LE 路径，长度 = plen * 2
```

```python
import struct, datetime
d = open(i_file, "rb").read()
size = struct.unpack_from("<Q", d, 8)[0]
ft   = struct.unpack_from("<Q", d, 16)[0]
plen = struct.unpack_from("<I", d, 24)[0]
when = datetime.datetime(1601,1,1) + datetime.timedelta(microseconds=ft/10)
orig = d[28:28+plen*2].decode("utf-16-le","replace").rstrip("\x00")
r_file = "$R" + i_name[2:]          # $I0000CX.json -> $R0000CX.json
```

> 按**字节**解析 `plen` 会得到被截断的假路径，导致误报「回收站 0 命中」——
> 这个坑实测浪费了一整轮排查。

**事故定性技巧**：看回收站条目的**删除时间直方图**。机器批量删除会呈现
稳定速率（实测 ~1,280 文件/分钟）且窗口与可疑命令的运行时间完全重合。

---

## 判定「不是 git 干的」的证据链

给用户解释根因时，这几条最有说服力：

1. `.git/logs/HEAD`（reflog）里**没有**该命令的条目 —— 它在写 reflog 之前就被杀了；
2. `git status` 只有 ` D`、**没有 ` M`** —— 现存文件内容与 HEAD 一致，未被回滚；
3. git 删除工作区文件走 `unlink`，**不进回收站**；而文件确实在回收站里；
4. 连 `.git/index.lock` / `HEAD.lock` / `packed-refs.lock` / `AUTO_MERGE.lock`
   **本身也被回收了** —— git 绝不会这样处理自己的锁；
5. 目标分支与 HEAD 的指向**始终未变**，无 `MERGE_HEAD` —— 操作根本没生效。

---

## 硬约束速查

| 禁止 | 改用 |
|---|---|
| `git switch` / `git checkout <branch>` | `git update-ref refs/heads/<b> <base>` **再** `git symbolic-ref HEAD refs/heads/<b>`（漏 `update-ref` ⇒ **未出生分支**；先 `git worktree list` 确认该分支未被别处检出） |
| `git switch main && git merge --no-ff <b>` | `git commit-tree` + `git update-ref` + `symbolic-ref` |
| `git restore -- .` / `git checkout -- .` | 按 ≤3,000 文件/命令的 `--pathspec-from-file` 分块 |
| 把长时 git 命令丢后台跑 | 把命令**本身切小**（前后台都一样会被 SIGTERM） |
| `git reset --hard` 救场 | 先查对象库，再 `git restore` |
| `wmic` | `tasklist \| grep -i "git\.exe"`（+3–5 次重试） |

## 收尾必做（本技能专属项）

> 通用收尾序列见 `_handoff/DEFINITION-OF-DONE.md`（相对 JVS 仓库根）：
> 编辑存活核验 / 双门禁 / 分支与 plumbing 合并 / 回读 HEAD / 工作区三条 / 查版本库不查磁盘。

1. **第 4 步的逐字节校验**必须做完（见上文「第 4 步：逐字节校验」）。
2. **把事故与铁律写进项目的治理文档**（否决项登记表 + 操作铁律章节），
   **不要只留在会话里** —— 下一个接手的人不会看到这次对话。
3. **提交前 `git worktree list` + `git symbolic-ref --short HEAD` 必验**
   —— worktree 与主工作树不得同时检出同一分支，见 `GIT-POLICY.md` §十三。
4. **正文才是详解**：分支名扁平（第 2 步）、提交后回读 HEAD（第 2 步）、
   `git read-tree HEAD` 索引对齐与「查版本库不查磁盘」（`GIT-POLICY.md` §十二）。
