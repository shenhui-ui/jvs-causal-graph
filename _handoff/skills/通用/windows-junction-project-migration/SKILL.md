---
name: windows-junction-project-migration
description: 在 Windows 上把一个项目目录迁到新位置，同时让所有硬编码绝对路径继续可用。用「真实字节移动 + 原路径留 Junction」手法，零代码改动、完全可回滚。当用户说「把项目挪到 X 文件夹」「整理项目位置」「迁移工作区」「目录太乱想分区」且项目里有大量硬编码路径引用时使用。
agent_created: true
---

# Windows 项目迁移（Junction 影子路径法）

## 何时用

- 用户要把项目目录搬到新位置（如桌面某个文件夹），并做功能分区
- 项目内部存在**大量硬编码绝对路径**（脚本、台账、配置、JSON 里的路径字段）
- 用户不想（或不应该）批量改写路径

**不适用**：跨盘/跨机器迁移（junction 不能跨卷）、需要版本库历史重写的场景。

## 核心思路

```
真实字节 → 新位置
原路径   → Windows Junction 指向新位置
```

效果：
- 所有绝对路径经 junction 透明解析 → **行为不变**
- 相对路径 → Windows 保留逻辑路径 → **行为不变**
- 文件 SHA / 台账 / 校验器 → **全部不变**
- 删除 junction 即可移回 → **完全可回滚**

同盘移动是 MFT 级操作，**几 GB 也是秒级**，没有「复制到一半失败」的风险。

## 前置勘察（必做）

```python
import ctypes
GFA = ctypes.windll.kernel32.GetFileAttributesW
GFA.restype = ctypes.c_uint32          # ← 必须！见「坑 1」
GFA.argtypes = [ctypes.c_wchar_p]
a = GFA(path)                          # 0xFFFFFFFF 表示不存在
is_reparse = a != 0xFFFFFFFF and bool(a & 0x400)   # FILE_ATTRIBUTE_REPARSE_POINT
```

要确认的事：
1. **目标盘与源同卷**（否则 junction 无效，只能真复制）
2. **目标父目录不是 OneDrive / 重定向目录**（正属性值，非 reparse，如 `0x10`）
3. 硬编码路径引用量 —— 用 Grep 统计，量大就别考虑改写方案
4. 磁盘剩余空间

## 执行步骤

```
S1  只读基线：全量文件清单 + 关键文件 SHA256        ← 一定要先做
S2  建分区目录
S3  移动真实字节（os.rename 优先，失败退 MoveFileExW）
S4  原路径创建 Junction（cmd /c mklink /J <link> <target>）
S5  校验：junction 可达 + 关键 SHA 比对 + 物理位置交叉验证 + 文件数复核 + 读写探针
S6  写导航 README + 迁移记录（含回滚命令）
```

### 移动函数（含降级）

```python
def move_one(src, dst):
    try:
        os.rename(src, dst)
        return True, "rename"
    except OSError as e1:
        MF = ctypes.windll.kernel32.MoveFileExW
        MF.restype = ctypes.c_int
        MF.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        if MF(src, dst, 1 | 2 | 8):    # REPLACE_EXISTING|COPY_ALLOWED|WRITE_THROUGH
            return True, "MoveFileEx"
        return False, str(e1)
```

### 建 Junction

```python
import subprocess
subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True)
```

> `capture_output=True` 很重要：cmd 会输出 GBK 字节，默认 UTF-8 读取会抛
> UnicodeDecodeError（无害的线程警告，但会污染输出）。

## 坑（实测踩过，务必规避）

### 坑 1：`GetFileAttributesW` 不声明 `restype` → 存在性误判

ctypes 默认把返回值当**有符号 int**，`INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF` 变成 `-1`，
于是 `a != 0xFFFFFFFF` 恒为真 —— 「路径不存在」被误判成「已存在」，脚本静默跳过全部移动。

**必须**：`GFA.restype = ctypes.c_uint32`。

### 坑 2：当前工作目录 / 脚本自身所在目录无法 rename

如果脚本放在待迁移目录内，**进程持有该目录句柄**，`os.rename` 报
`WinError 5 拒绝访问`。

**排查**：对源目录的每个子项做「改名再改回」探针，锁定被占用的子目录：

```python
for e in os.listdir(R):
    p = os.path.join(R, e); q = p + "__lckprobe"
    try: os.rename(p, q); os.rename(q, p)
    except OSError: print("LOCKED:", e)
```

**解法**（按优先级）：
1. 把迁移脚本放到目标区（新位置）执行，别放源目录内
2. 若**目录本身**被外部进程（宿主 App、编辑器）持有，无法移动 —— 改为
   **逐子项搬迁 + 子项级 junction**，保留该目录为实体目录。效果等价。
3. 单一文件类子项可留在原地不迁

### 坑 3：先 dry-run

迁移脚本必须支持 `--dry`，先空跑核对每条 `move` 的源/目标，确认无误再正式执行。
并且要支持**续跑**（已移动的补建 junction，未移动的继续移动），因为中断很常见。

## 校验（S5）必查项

| 项 | 方法 |
|---|---|
| junction 可达 | `is_reparse(p) and os.path.isdir(p)` |
| 关键 SHA 一致 | 经**原路径**读取并与 S1 基线比对 |
| 物理位置正确 | 在**新位置**直接读取（不经 junction） |
| 文件数复核 | 新位置递归计数 vs S1 总数 |
| 可读写 | 在原路径写探针 → 读 → 删 |

> **额外的强校验**：跑项目自带的校验器/单元测试。即使有失败也先别慌——
> 要判定它是否**迁移前就红**。方法：看失败用例是否只涉及业务逻辑（与路径无关），
> 并单独探测其根因。迁移引入的失败通常表现为「文件找不到 / 路径解析异常」。

## 回滚

```cmd
rmdir "<旧路径>"                                   :: 只删联接
move "<新路径>" "<旧路径>"                          :: 移回真实数据
```

> ⚠️ **必须用 `rmdir`（删除目录联接），绝不可用 `rmdir /s /q` 或 `rm -rf`** —— 那会删掉真实数据。
> PowerShell 的 `Remove-Item` 对 junction 默认行为需确认，优先用 `cmd /c rmdir`。

## 交付文档模板

- `README.md`：分区导航（每个区是什么 / 进来先看哪个文件）+ 当前状态 + 已知坑
- `MIGRATION-RECORD.md`：迁移前后对照表、Junction 清单、S5 校验结果、回滚命令、明确「未做的事」

## 纪律

- 迁移前**必须**有只读 SHA 基线，否则无法证明「没丢数据」
- **不删除任何文件**；不批量改写路径；不使用 `rm -rf`
- 涉敏目录单独分区并在 README 标注访问纪律

---

## 后续请求：「按功能重新分类文件夹」

迁移完用户常接着提这个。**先别动手搬目录**，先测量目标目录是不是全系统引用锚点：

```python
EXT_CODE = {'.py', '.js', '.sh'}
EXT_DATA = {'.json', '.jsonl', '.db', '.csv'}
# 统计有多少代码/数据文件引用了这些路径名
```

判断标准（实测量级参考）：

| 引用文件数 | 结论 |
|---|---|
| < 10 | 可以搬，同步改引用 |
| 10 ~ 100 | 谨慎；先分清是「代码真引用」还是「文档提及」 |
| > 100，或有**裸相对段** | **不要搬** —— 改建索引层 |

**致命信号**（出现任一即放弃物理重排）：
1. 路径以**裸相对段**出现（如 `docs\host_memory_dump`，无 `../` 前缀）→ 按 CWD 解析，搬到哪都崩
2. 目录内脚本**同目录互相 `import`** → 移动即断模块解析
3. 台账/校验器把路径写进了 `.json`/`.jsonl` 数据文件内

### 索引层方案（推荐）

物理层 0 改动，叠加一份标注：

```
_index/                # 文件清单**随项目而定**，以下是 JVS 的实际形态（示例）
├── 文件索引.jsonl     # 逐文件 {p, l1, l2, s}
├── 分类统计.json       # 各类计数（权威数字源）
├── 功能分类总表.md     # 人类可读的 大类 × 子类 总表
├── 功能进度表.md       # 只列进度的精简版
└── 一次性脚本登记表.md # 全库 .py 的生命周期（活件 / 一次性 / 可弃）
_migrate/classify.py   # 分类规则，可改后重跑
```

⚠️ 索引层通常是**生成物**（由 `_migrate/` 下的生成器脚本产出，勿手编）；
若拆成多个生成器，**执行顺序往往不可换**，且要在跑之前先 `git add`（产物里可能内嵌
「是否已入库」这类由 `git ls-files` 推导的标记）。

写规则时注意：
- **规则顺序即优先级**：宽泛规则（如「某目录下剩余全部」）必须排在精细规则之后，否则会吞掉后面的分类
- 分类维度按**功能**而非创建时间：用「做什么」（产品定义 / 技术实现 / 原料 / 实验 / 治理 / 执行 / 复核 / 协作 / 工程 / 受限）而不是「哪一天建的」
- 目标：未归类数 = 0。跑完打印统计，逐条修规则直到清零

**Python 字符串陷阱**：`r"...\\"` 是**字面双反斜杠**（`\\` 在 raw 串里不折叠），
用作路径分隔符匹配会全部失配。用 `"\\"` 拼接或改用正斜杠。

### 交付文档补充

若做了索引层，README 要加一节说明**为什么功能分类不直接改建文件夹**（附锚点引用数与原因），
否则后人会重复踩坑，把索引层误当成「没做完」。同时把索引层入口放进 README 显眼位置。

---

## 再后续：「标注功能进度」+「做接手包」

分类做完，用户常接着要这两件。它们都属于**接手友好化**。

### 标注进度

用固定状态词表（用户常指定），**状态必须来自一手证据，不能推测**：

| 状态 | 判定标准 | 证据来源 |
|---|---|---|
| **已开发** | 产物已存在**且已验证/已闭环** | 验收台账、交付收据、报告结论 |
| **开发中** | 产物存在但**未闭环**（有待办/待审/待授权） | 交付物的 `status` 字段（如 `draft_for_user_review`） |
| **待开发** | 已有设计或提案，尚未产出 | 计划文档里的「待审/待授权」条目 |
| **待规划** | 尚未立项或无设计 | 路线图阶段表 |

要点：
- **大类进度 = 子类中最未完成者**（木桶口径），否则会掩盖待办
- 状态定义单独放一个 `status_map.py`，`(l1, l2) -> (状态, 依据一句话)`，改完重跑
- **必须标注依据**，否则三个月后没人知道这个「已开发」是怎么判的

**典型坑**：别把「有产物」等同于「已完成」。
本项目里复核机械看起来跑完 19 轮，实际交付物仍 `draft_for_user_review`；
反过来，看起来「有 123 条未结项」的台账，其实 **0 阻塞项**、单元/事件 **100% 闭环**。
**必须去读台账的结构化字段，不能凭条目数判断进度。**

### 做接手包（`_handoff/`）

目标：新会话**不用问人**就能开工。最小集：

```
_handoff/
├── HANDOFF.md          # 接手总览：项目是什么/当前在哪/下一步
├── PENDING.md          # 待办 + 已知缺陷
├── env/
│   ├── ENV.md          # 解释器/凭证/模型通道/环境变量全表
│   ├── RUN-CONTRACT.md # ★ 脚本必须在哪个目录跑
│   └── check_env.sh    # ★ 环境自检（接手第一条命令）
── scripts/SCRIPTS.md  # 脚本登记表：入口/用法/是否联网
```

**`check_env.sh` 要有这几项**（每项给 OK/WARN/FAIL 计数，结尾给结论）：
解释器版本 → 关键脚本可编译 → 离线回归能过 → 数据资产就位 →
凭证闸门（有/无）→ 外部依赖 → **迁移完整性（Junction 是否还在）**。

**`RUN-CONTRACT.md` 是最容易被忽略但最要命的一份**：统计有多少脚本用了
**CWD 相对路径**（本项目 178 个）或写死的老绝对路径。若不写清，
接手者从根目录跑就会全线找不到文件。

### 工作区配置（`.workbuddy-ai/`）

把项目记忆与技能也放进项目内，新会话自动可用：
- `.workbuddy-ai/memory/MEMORY.md` —— 汇总项目铁律、关键数字、已知陷阱
- `.workbuddy-ai/skills/` —— 从用户级技能拷入（项目专用 + 通用）

汇总记忆时**顺手标注过期点**（如「原记忆说 v0.2 是现行版，实际已是 v0.3」），
这比删掉旧内容安全 —— 保留脉络，同时防止误用。

---

## 第四阶段：「git 化 + 分批提交 + 主线无污染」

用户原话常是「能确保后续改动可查吗？定一下，开发要在主线上开分支，测试通过才合入」
「按照进度分批 git，确定主线是正确且无污染」。

### 第一原则：先量准体积，别被工作树吓到

**这是最容易犯的错，务必先做。** git 存储 = zlib 压缩 + 内容寻址去重，
`工作树 3.7 GB` 完全可能只占 `库内 45 MB`。

测量方法（逐文件 sha256 去重 + zlib 压缩后求和）：

```python
uniq = {}   # sha256 -> zlib 压缩后长度
for f in files:
    raw = open(f, "rb").read()
    uniq[hashlib.sha256(raw).hexdigest()] = len(zlib.compress(raw, 6))
repo_size = sum(uniq.values())
```

本项目实测结果（务必引以为戒）：

| 类别 | 工作树 | 唯一内容 | 库内 |
|---|---|---|---|
| `.sse` 流式原文 | 3,530 MB / 2,363 | 207 | **5.0 MB** |
| `.zip` 打包件 | 46 MB / 43 | 38 | 42.8 MB |
| 全库合计 | 3,902 MB / 21,166 | 5,253 | **约 78 MB** |

**结论：原本建议"排除 .sse 省 96%"是错的** —— 省下 48 MB，
却丢 5.8% 被放弃尝试的思考链 + `verify_delivery.py` 对 `.sse` 的校验能力
（glob 空列表 → 断言静默跳过）。**默认建议全纳入，只排缓存/日志/凭证。**

判断"能不能排"的硬标准 —— 查三件事：
1. **是否被清单/校验文件点名**：`grep` 待排除文件名是否出现在
   `integrity.json` / `*.meta.json` / manifest 中（本项目 2,361 个文件被引用）
2. **是否有代码 glob 它**：`*.sse` 被 `verify_delivery.py` glob + 哈希断言
3. **内容是否在别处存在**：`.sse` 与其 `payload.response.json` 逐一配对比对
   （本项目 400 组：377 完全一致、23 不一致且集中在同几个 chunk ——
   那是被放弃的尝试，`response.json` 是最终采纳版）

### 第二原则：内嵌 `.git` 必须先处理

`git add -A` 遇到内嵌仓库会警告 `adding embedded git repository`，
并且**只记一个 gitlink（mode 160000）**，整个子树的文件内容根本不入库。

```bash
git ls-files -s 子目录 | grep -c '^160000'   # >0 说明内容没进去
```

处理方案（保留历史）：`git subtree add`
```bash
cd 外层仓库
git subtree add --prefix=子目录路径 "C:/绝对路径/到/内嵌仓库" main
```
- **路径必须用 Windows 盘符格式** `C:/...`；MSYS 格式 `/c/...` 与
  `file:///C:/...` 都会报 `does not appear to be a git repository`
- 不加 `--squash` 才能保留原始 commit hash
- 目标 prefix 不能已存在 → 先把目录移出，导入后再补未追踪文件

### 第三原则：行尾必须锁死（否则破坏产物可核验性）

三重配置，缺一不可：
```bash
git config --local core.autocrlf false
git config --local core.eol lf
```
```
# 根 .gitattributes
* -text
```

**关键陷阱**：若子树的**原生 `.gitattributes` 含 `text=auto`**，
签出时会把 CRLF 归一化为 LF，**静默改变文件字节** ——
本项目 408 个文件被改，导致冻结语料 / 图谱 / 题集指纹全部漂移。

两种应对，选后者：
- ❌ 让子树保留 `text=auto`：`* -text` 会让 412 个已归一化的文件
  浮出为「伪 modified」（实测 65.1%），工作区长期脏
- ✅ **在子树自己的 `.gitattributes` 末尾追加 `* -text`**，
  嵌套属性优先级更高，彻底覆盖其原生规则

建立**冻结指纹基线**（`scripts/frozen-fingerprints.json`）并在门禁里复核，
这是"字节冻结真的生效了吗"的唯一证据。

### 第四原则：pre-commit 钩子只查暂存文件

**本项目返工了三次，全是可预见的坑：**

| 写法 | 问题 |
|---|---|
| shell `grep` 逐文件扫全库 | 7 千个大 JSON → commit 卡 10 分钟 |
| `mktemp` 临时文件传给 Python | MSYS 与原生 Python 路径解析不一致 → `FileNotFoundError`，commit 被误中止 |
| `printf \| python <<HEREDOC` | **stdin 被 heredoc 抢占**，管道数据丢失 → hook 永远看到 0 个文件，**门禁形同虚设** |
| ✅ Python 直接 `subprocess.run(["git","diff","--cached","--name-only","--diff-filter=ACM"])` | 无临时文件、无 stdin 竞争；13,038 文件 7 秒 |

**务必实测钩子真的能拦截**：故意 `git add -f` 一个禁止文件，确认被拒。
否则你只是在自欺欺人。

### 第五原则：`git gc` 必须在 commit 之后

在暂存区有未提交文件时执行 `git gc --prune=now`，会**剪掉待提交的 blob**，
导致 `commit` 报 `invalid object ... Error building trees`。
磁盘文件不丢，`git reset` 后重新 `git add` 即恢复。
另外**不要设 `gc.auto 0`** —— 本次导致松散对象累积到 83 MB。

### 环境限制：分支名不得含斜杠

`git 2.55.0.windows.3` 实测：`git branch feat/xxx` **静默返回成功但不创建引用**，
预先手动 `mkdir refs/heads/feat/` 也无效。扁平名 `feat-xxx` 全部正常。

**统一用 `类型-名称` 扁平命名**，并在政策文档里写明，否则新会话会踩同一个坑。

若 `checkout -b` / `checkout -f` 中途被中断，会留下索引残留（表现为全库 `D`）：
```bash
# 磁盘文件完好，只是索引态错了 —— 不要 checkout -f 重写全库
git read-tree HEAD      # 直接把索引重置回 HEAD，不动磁盘
```

### 交付物清单（缺一不可）

| 文件 | 作用 |
|---|---|
| `GIT-POLICY.md` | 仓库边界 / 字节冻结 / 分支模型 / 门禁 / 提交规范 / **回退方式** / 为何不用 submodule |
| `scripts/git-gate.sh` | 合入前门禁（解释器 / py_compile / 离线回归 / 指纹 / 凭证 / JSON / 工作区） |
| `scripts/scan-secrets.sh` | 凭证扫描（**只查是否被追踪**；磁盘上有凭证文件是正常的，排除≠删除） |
| `scripts/frozen-fingerprints.json` | 冻结产物 SHA256 基线 |
| `.githooks/pre-commit` | 只查暂存文件，快速拦截 |
| `_migrate/final_audit.py` | 主线终检（清洁度 / 无污染 / 大文件 / 体积 / 指纹 / junction / 分支模型） |

### 分批提交的顺序（按功能进度）

```text
① chore(repo)  治理骨架      .gitignore/.gitattributes/GIT-POLICY/scripts/.githooks
② 导入内嵌仓库   subtree add  保留原始历史
③ fix(repo)    字节回归修复  若子树 text=auto 已污染字节
④ feat(<模块>)  各业务分区分批  01 主机 → 02 评测 → 03 工作区
⑤ docs(handoff) 接手包/索引/工具
⑥ chore(repo)  终检脚本
```
每批提交前 `git status` 核对，避免夹带无关文件。**强制的跳过顺序**：
受限素材独立仓库、`gc` 最后做、标签在全部提交后打。

### 终检必须验证「无污染」

- 受限素材被追踪数 = 0
- 凭证类被追踪数 = 0（注意：扫描脚本自身文件名含 `secrets` 会误报，需白名单）
- `>10MB` 的 tracked 文件数 = 0
- 工作区 0 未提交 / 0 未跟踪（见 `_handoff/DEFINITION-OF-DONE.md` §6）
- 冻结指纹 100% 一致
- junction 全部可达
- **真跑一遍分支流程**：开分支 → 改文件 → 提交 → 门禁 → `--no-ff` 合并 → 删分支。
  没跑过的政策等于没有政策。

---

## 第五阶段：「`git` 被中断操作损坏后的恢复」

**触发**：`.git/refs` 消失 / `fatal: not a git repository` / `objects/pack` 只剩 `.idx`。
最常见的诱因是**强制终止了 `git merge` / `git gc` / `git checkout`**（长时间无输出被误判为卡死）。

### 铁律：绝不中断 git 命令

本环境实测两次，中断 `git merge` 后 `.git/refs/` 整个目录消失，
配套 51.87 MB 的 `.pack` 被移入回收站。**无输出 ≠ 卡死**；
长命令放后台跑并观察，不要 kill。（kill 后还会殃及工作区内近期写入的文件。）

### 恢复顺序（不可颠倒）

```
先保全现场 → 再找 pack（含回收站）→ 再依 reflog 重建 refs → 再重建索引
```

**第 1 步 保全现场**：立刻把受损 `.git` 的关键件复制到**工作区之外**
（`HEAD` / `config` / `index` / `packed-refs` / `logs/` / `objects/pack/`）。
不要先删再想办法。

**第 2 步 找 pack**。先全盘搜：

```bash
find /c/Users -name "*.pack" -o -name "tmp_pack_*" 2>/dev/null
```

找不到就**查回收站** —— 这是本项目破案的关键。`.pack`/`.idx` 常被「移动」进回收站：

```python
# 读 $I 元数据（8B 头 + 8B 大小 + 8B 时间 + 4B 名称长度 + UTF-16LE 路径）
import struct
d = open(meta, "rb").read()
size = struct.unpack("<q", d[8:16])[0]
nlen = struct.unpack("<i", d[24:28])[0]
orig_path = d[28:28 + nlen*2].decode("utf-16-le")
```

也可直接按魔数找：

```python
if open(p, "rb").read(4) == b"PACK": ...
```

**第 3 步 原生校验 pack（不依赖 git，此时 git 还不可用）**：

| 检查 | 判据 |
|---|---|
| 头部 | `PACK` + version(2/3) + object count |
| 自校验 | 尾部 20 B sha1 == 对前 `len-20` 字节求的 sha1（**且通常等于文件名里的哈希**） |
| idx 配对 | `.idx` 尾部 40 B 前 20 B 记录的 pack sha1 == 上一步实测值 |
| idx 尺寸 | `8 + 1024 + 28N + 40 == idx 字节数`（v2 idx 格式） |
| 完整性 | 逐对象 `zlib.decompress`，成功数 == 声明数，末尾剩余 0 字节 |

> **性能坑**：解压循环里写 `d.decompress(data[pos:])` 会对整个 pack 做 N 次切片拷贝
> （6722 × 52 MB ≈ 343 GB 内存搬运，进程会被 OOM/kill 且**无任何输出**）。
> 必须用 `memoryview(data)` 零拷贝。脚本调试期一律加 `-u` 让输出实时可见。

**第 4 步 重建 refs（pack 里可能没有最新的提交）**：

- **权威来源是 `.git/logs/refs/**`（reflog）**，逐文件读**最后一条**记录的目标 sha
  ——即使 ref 文件没了，reflog 通常还在。
- `.git/info/refs` 与 `.git/packed-refs` 可交叉验证，且 **`info/refs` 常保留
  被删标签的 tag object SHA**（`packed-refs` 可能已过时缺失）。
- 逐个写回 `.git/refs/heads/<name>`，然后**必须确认 `.git/refs` 目录存在** ——
  `git` 的 `is_git_directory()` 要求它存在，缺了就直接报「不是仓库」。
- 若 reflog 里的 sha 在 pack 中不可用，用 `.idx` 列出全部 sha 做集合比对，
  把 `main` **复位到 pack 中实际存在的那个提交**，其余提交的内容从工作树重建。

**第 5 步 重建索引**：`git read-tree HEAD`（只改索引，**不动磁盘一个字节**）。
不要用 `checkout -f`（本环境易中断，留下全库 `D` 残影）。

**第 6 步 收尾**：`git fsck --full` 应只剩无害 `dangling`；
清理引用丢失对象的 reflog 行；把重建好的 `.git` 另存一份到**工作区之外**作黄金副本。

### 加固：避免再次触发

```bash
git config gc.auto 0                  # 禁止自动 gc 擅自 pack/清 ref
git config maintenance.auto false     # 禁止后台维护
git config core.fsmonitor false       # Windows 上易异常
git config core.logAllRefUpdates true # 保住 reflog —— 它是自愈依据
```

### 环境特性：工作区内的改动可能被回滚

本环境存在「修改前备份 / 回滚」机制（`.workbuddy-ai\workspace\sessions\*\.modify_backup*`），
会清除工作区内较新的写入。实测：工作区**外**的探测文件全部存活，
而工作区**内**对已跟踪文件的改动被还原成旧版。

**推论**：

- 恢复件、结论文档、工具应放在**工作区之外**（如 `C:\Users\<user>\_staging\`）。
- 要落库的改动，把「改文件 + `git add` + `git commit`」压在**一次脚本运行内**完成，
  减少暴露窗口。
- 已提交的内容不会丢 —— 内容已在对象库里，用 `git restore --source=HEAD -- <path>` 取回。
  **不要**用 `git checkout HEAD -- <path>`：本环境 `git switch` / `git checkout` 必被 SIGTERM，
  而 SIGTERM 会把工作区文件**批量移入回收站**。详见技能 **`git-sigterm-safe-recovery`**。