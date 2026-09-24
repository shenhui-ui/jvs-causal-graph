# -*- coding: utf-8 -*-
r"""JVS 仓库侦察统一入口（只读）。

合并自 `git_recon.py` / `git_recon2.py` / `git_recon3.py`。三个原脚本是**三轮不同侦察**，
共享同一段骨架（Windows reparse point 判定 `is_reparse()`、`os.walk` + 跳过集、
`run()` 子进程包装、按 sha256 算内容重复率），差异在**侦察项**。故本脚本按**能力**拆子命令，
而不是按原脚本拆 —— 后者只是「三个文件塞进一个」，前者才真的消掉了重复。

| 子命令 | 侦察项 | 原脚本 |
|---|---|---|
| `links`           | JVS 内部 junction / 符号链接（git 是否越界索引） | recon1 §1 |
| `cred`            | 凭证 / 会话文件名扫描 | recon1 §2 |
| `secrets`         | 文本文件秘密模式扫描（sk- / ghp_ / AKIA / 赋值式） | recon1 §3 |
| `restricted`      | `04-restricted-materials/` 内容盘点 | recon1 §4 |
| `exclude-sizing`  | 拟排除规则下的可提交体积估算 | recon1 §5 |
| `git-capability`  | git 工具能力确认 | recon1 §6 |
| `ws-links`        | 工作区父目录 / 工作区 / 四个分区的 junction 列表 | recon2 §1 |
| `nested-git`      | JVS 内所有 `.git`（嵌套仓库 / gitlink 风险） | recon2 §2 |
| `images-big`      | 图片分布 + >5 MB 单文件 | recon2 §3 |
| `repo01`          | `01-host-product/2026-08-28-20-59-40` 仓库状态 | recon2 §4 |
| `disk-links`      | 全盘 junction 定位（起点 `C:\Users\<user>`，跳过 JVS） | recon3 §1 |
| `ws-config`       | `JVS\.workbuddy-ai` 内容清单 | recon3 §2 |
| `longest-path`    | 最长路径 / 是否超 Windows MAX_PATH | recon3 §3 |
| `dup`             | 内容重复率（**`--root` 参数化**，原 recon3 §4/§5 是同一段代码抄两遍） | recon3 §4+§5 |

用法：
    python _migrate/repo_recon.py links
    python _migrate/repo_recon.py dup --root 02-m1-evaluation
    python _migrate/repo_recon.py --list

⚠️ 只读。`disk-links` / `dup` / `exclude-sizing` 会遍历数 GB，耗时较长。

---
**合并口径（与原文的差异，逐条可核）**

`dup` 的 `--top N`（默认 8）控制末尾「Top N 冗余内容」明细块。
- 原 `git_recon3.py` 的 §4（02 分区）与 §5（03 分区）**代码抄了两遍，但 §5 漏了末尾的 Top 明细循环**
  —— 同一段逻辑一个有一个没有，属历史遗漏而非有意设计。
- 合并后 `--root` 统一带 Top 明细（默认 `--top 8`）。
- **`--top 0` 可逐字复现原 §5**（只 3 行汇总）；`--top 8` 逐字复现原 §4。
  两个原始节均可用显式参数精确复现，见 `_staging/verify-repo-recon-merge.py`。

`dup` 的哈希改走 `hash_cache.file_sha256()`（**分块读**，替代原文的
`hashlib.sha256(open(p,"rb").read())` 整文件读入）。**摘要逐位相同**（sha256 是流式算法），
区别只在峰值内存。另有 `--cache PATH` / `--no-cache`（阶段 B / item 3）：

- **默认 `--no-cache`** ⇒ 不加参数时行为与合并前**逐字一致**，A/B 可直接比对。
- `--cache PATH` 开启记忆化（键 = 路径 + size + mtime_ns），并在末尾打印命中统计。
- ⚠️ 缓存**不得**用于校验语义的调用方；禁区清单见 `hash_cache.py` 模块头。
"""
import argparse
import ctypes
import os
import re
import subprocess
import sys

# 与 hash_cache.py 同目录 ⇒ 直接脚本方式运行时 sys.path[0] 即本目录；
# 被当作模块 import 时（如验证脚本）兜底把本目录加进 path。
# ⚠️ **硬依赖、不静默降级**：模块缺失就让它 ImportError 报出来，
#    而不是在本地再抄一份 `file_sha256` 造成两处实现漂移。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from hash_cache import HashCache, file_sha256       # noqa: E402  (需先修 sys.path)

JVS = r"C:\Users\<user>\Desktop\JVS"
P01 = os.path.join(JVS, "01-host-product", "2026-08-28-20-59-40")
P04 = os.path.join(JVS, "04-restricted-materials")

FILE_ATTR_REPARSE = 0x400
SEP = "=" * 70

# 通用跳过集（原脚本各处的并集；各子命令按需取子集）
SKIP_BASE = {".git", "__pycache__", "node_modules"}
SKIP_WIDE = SKIP_BASE | {".workbuddy-ai", "$RECYCLE.BIN", "System Volume Information"}


def is_reparse(path):
    """判定 junction / 符号链接（Windows reparse point）。

    ⚠️ `restype` 必须显式声明为 `c_uint32`：否则返回值被当作有符号 int，
    `-1 != 0xFFFFFFFF`，会把「路径不存在」误判为「已存在」（2026-09-16 实测踩坑）。
    """
    try:
        GFA = ctypes.windll.kernel32.GetFileAttributesW
        GFA.restype = ctypes.c_uint32
        GFA.argtypes = [ctypes.c_wchar_p]
        a = GFA(path)
        if a == 0xFFFFFFFF:
            return None
        return bool(a & FILE_ATTR_REPARSE)
    except Exception:
        return None


def run(cmd, cwd=None, timeout=60):
    try:
        r = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return "ERR %s" % e


# --------------------------------------------------------------------------
# recon1 的能力
# --------------------------------------------------------------------------

def cmd_links():
    """JVS 内部 junction / 符号链接扫描。"""
    print(SEP)
    print("1. JVS 内部 junction / 符号链接扫描")
    print(SEP)
    links = []
    scanned = 0
    for root, dirs, files in os.walk(JVS):
        scanned += 1
        for d in list(dirs):
            p = os.path.join(root, d)
            if is_reparse(p):
                links.append(("DIR", p))
                dirs.remove(d)          # 不跟随，防成环
        for f in files:
            p = os.path.join(root, f)
            if is_reparse(p):
                links.append(("FILE", p))
    print("扫描目录数: %d" % scanned)
    if links:
        for k, p in links:
            print("  [%s] %s" % (k, p))
    else:
        print("  未发现任何 junction / 符号链接  -> git 不会越界索引")


CRED_NAMES = [".doubao_session.json", ".env", ".env.local", "credentials.json",
              "credentials", "id_rsa", "id_ed25519", ".netrc", "token.json",
              "auth.json", "secrets.json", "service-account.json",
              "cookie", "cookies.json", "sensenova.json", "relay.json"]


def cmd_cred():
    """凭证 / 会话文件名扫描。"""
    print(SEP)
    print("2. 凭证 / 会话文件扫描")
    print(SEP)
    found = []
    for root, dirs, files in os.walk(JVS):
        dirs[:] = [d for d in dirs if d not in (".git",)]
        for f in files:
            low = f.lower()
            if low in CRED_NAMES or low.endswith(".pem") or low.endswith(".key"):
                p = os.path.join(root, f)
                try:
                    sz = os.path.getsize(p)
                except Exception:
                    sz = -1
                found.append((p, sz))
    for p, sz in found:
        print("  %8d B  %s" % (sz, p))
    if not found:
        print("  无命中")


SECRET_PATS = [
    ("sk-", re.compile(r"sk-[A-Za-z0-9_\-]{16,}")),
    ("ghp_", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("AKIA", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("ai-", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("assign", re.compile(r"(?i)(api[_-]?key|secret|passwd|password|token)\s*[:=]\s*[\"'][^\"']{16,}[\"']")),
]
SECRET_SKIP_EXT = {".sse", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".db", ".sqlite",
                   ".pyc", ".ico", ".woff", ".woff2", ".pdf", ".xlsx", ".docx"}


def cmd_secrets():
    """文本文件秘密模式扫描。"""
    print(SEP)
    print("3. 文本文件秘密模式扫描（sk- / ghp_ / AKIA / 赋值式密钥）")
    print(SEP)
    hits = {}
    nf = 0
    for root, dirs, files in os.walk(JVS):
        dirs[:] = [d for d in dirs if d not in SKIP_WIDE]
        for f in files:
            if os.path.splitext(f)[1].lower() in SECRET_SKIP_EXT:
                continue
            p = os.path.join(root, f)
            try:
                if os.path.getsize(p) > 4 * 1024 * 1024:
                    continue
                with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                    txt = fh.read()
            except Exception:
                continue
            nf += 1
            for name, rx in SECRET_PATS:
                for m in rx.finditer(txt):
                    hits.setdefault(name, []).append((p, m.group(0)[:60]))
    print("扫描文本文件数: %d" % nf)
    for name in hits:
        print("  --- %s: %d 命中 ---" % (name, len(hits[name])))
        seen = set()
        for p, s in hits[name]:
            key = (p, s)
            if key in seen:
                continue
            seen.add(key)
            print("    %s  ->  %s" % (p, s))


def cmd_restricted():
    """04 受限素材内容盘点。"""
    print(SEP)
    print("4. 04 受限素材内容盘点")
    print(SEP)
    for root, dirs, files in os.walk(P04):
        rel = os.path.relpath(root, P04)
        for f in files:
            p = os.path.join(root, f)
            try:
                sz = os.path.getsize(p)
            except Exception:
                sz = -1
            print("  %9d B  %s" % (sz, os.path.join(rel, f) if rel != "." else f))


EXCL_EXT = {".sse", ".zip", ".pyc", ".pyo", ".log", ".db-journal", ".db-shm",
            ".db-wal", ".tmp", ".bak", ".orig", ".rej", ".png", ".ico"}
EXCL_DIR = {".git", "__pycache__", "node_modules"}
EXCL_PATH_SUB = [".doubao_session.json"]


def cmd_exclude_sizing():
    """拟排除规则下的可提交体积估算。"""
    print(SEP)
    print("5. 拟排除规则下的可提交体积估算")
    print(SEP)
    stat = {}
    tot_n = tot_b = 0
    for root, dirs, files in os.walk(JVS):
        dirs[:] = [d for d in dirs if d not in EXCL_DIR]
        rel = os.path.relpath(root, JVS)
        top = rel.split(os.sep)[0]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in EXCL_EXT:
                continue
            if any(s in os.path.join(root, f) for s in EXCL_PATH_SUB):
                continue
            if f.endswith("~"):
                continue
            p = os.path.join(root, f)
            try:
                sz = os.path.getsize(p)
            except Exception:
                continue
            d = stat.setdefault(top, [0, 0])
            d[0] += 1
            d[1] += sz
            tot_n += 1
            tot_b += sz
    for k in sorted(stat):
        n, b = stat[k]
        print("  %-26s %7d files   %9.2f MB" % (k, n, b / 1048576))
    print("  %-26s %7d files   %9.2f MB" % ("TOTAL(可提交)", tot_n, tot_b / 1048576))


def cmd_git_capability():
    """git 工具能力确认。"""
    print(SEP)
    print("6. git 工具能力")
    print(SEP)
    print("  git --version :", run("git --version").strip())
    sub = run("git subtree -h").lower()
    print("  git subtree   :", "OK" if "usage" in sub or "subtree" in sub else "缺失")
    print("  git filter-repo:", run("git filter-repo --version").strip()[:80])
    print("  core.autocrlf :", run("git config --global core.autocrlf").strip())
    print("  user.name     :", run("git config --global user.name").strip())
    print("  user.email    :", run("git config --global user.email").strip())


# --------------------------------------------------------------------------
# recon2 的能力
# --------------------------------------------------------------------------

WS = r"C:\Users\<user>\host-workspace\2026-09-16-12-52-36"
WSROOT = r"C:\Users\<user>\host-workspace"


def _walk_links(base, label):
    print("-" * 70)
    print("%s: %s" % (label, base))
    print("-" * 70)
    if not os.path.exists(base):
        print("  (不存在)")
        return
    n = 0
    for entry in sorted(os.listdir(base)):
        p = os.path.join(base, entry)
        if is_reparse(p):
            n += 1
            try:
                tgt = os.path.realpath(p)
            except Exception as e:
                tgt = "<%s>" % e
            print("  [%s] %s" % ("D" if os.path.isdir(p) else "F", p))
            print("        -> %s" % tgt)
    if n == 0:
        print("  (无 junction)")
    print("  合计: %d" % n)


def cmd_ws_links():
    """工作区父目录 / 当前工作区 / 四个分区的 junction 列表。

    ⚠️ 这里的工作区是 **2026-09-16 的旧会话目录**（`WS`），不是 JVS。
    """
    _walk_links(WSROOT, "工作区父目录")
    _walk_links(WS, "当前工作区")
    for sub in ("01-host-product", "02-m1-evaluation", "03-d10-workspace",
                "04-restricted-materials"):
        _walk_links(os.path.join(WS, sub), "工作区\\%s" % sub)


def cmd_nested_git():
    """JVS 内所有 .git（嵌套仓库 / gitlink 风险）。"""
    print(SEP)
    print("JVS 内所有 .git（含嵌套仓库 / gitlink 风险）")
    print(SEP)
    gits = []
    for root, dirs, files in os.walk(JVS):
        if ".git" in dirs:
            gits.append(os.path.join(root, ".git") + "  [DIR]")
        if ".git" in files:
            gits.append(os.path.join(root, ".git") + "  [FILE]")
        dirs[:] = [d for d in dirs if d != ".git"]
    for g in gits:
        print("  " + g)
    print("  合计: %d" % len(gits))


IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")


def cmd_images_big(big_mb=5):
    """图片分布 + >N MB 单文件。"""
    print(SEP)
    print("图片 / 大文件分布（决定是否纳入版本库）")
    print(SEP)
    buckets = {}
    big = []
    for root, dirs, files in os.walk(JVS):
        dirs[:] = [d for d in dirs if d not in SKIP_BASE]
        rel = os.path.relpath(root, JVS)
        top = rel.split(os.sep)[0]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            p = os.path.join(root, f)
            try:
                sz = os.path.getsize(p)
            except Exception:
                continue
            if ext in IMG_EXT:
                d = buckets.setdefault(("IMG", top), [0, 0])
                d[0] += 1
                d[1] += sz
            if sz > big_mb * 1024 * 1024:
                big.append((sz, p))
    for (k, top), (n, b) in sorted(buckets.items()):
        print("  图片 %-26s %5d files  %8.2f MB" % (top, n, b / 1048576))
    print("  --- >%dMB 单文件 ---" % big_mb)
    big.sort(reverse=True)
    for sz, p in big[:25]:
        print("    %8.2f MB  %s" % (sz / 1048576, os.path.relpath(p, JVS)))
    print("    合计 >%dMB 文件数: %d" % (big_mb, len(big)))


def cmd_repo01():
    """01 子目录视角下的仓库状态（导入前留档）。

    ⚠️ **读数口径（2026-09-20 实测）**：`01-host-product/2026-08-28-20-59-40`
    自身**没有 `.git`** —— `git rev-parse --show-toplevel` 返回
    `C:/Users/<user>/jvs-src`。所以这里报的是 **JVS 主仓**，不是一个独立的 01 仓库；
    原快照（2026-09-19）同样是 `toplevel : C:/Users/<user>/jvs-src`。

    且两项口径不同，不可混读：
      - `tracked` = `git ls-files` 在 **cwd** 下运行 ⇒ 只数 01 子目录（实测 644）
      - `status`  = `git status` 是**整仓**范围 ⇒ 会列出 `_migrate/` 等仓根条目

    ⇒ 不要把 `tracked : 644` 读成「JVS 仓库有 644 个跟踪文件」（实际约 2.1 万）。
    """
    print(SEP)
    print("01 仓库当前状态（导入前留档）")
    print(SEP)
    print("  toplevel :", run("git rev-parse --show-toplevel", P01, 120).strip())
    print("  branch   :", run("git rev-parse --abbrev-ref HEAD", P01, 120).strip())
    print("  HEAD     :", run("git rev-parse HEAD", P01, 120).strip())
    print("  commits  :", run("git rev-list --count HEAD", P01, 120).strip())
    print("  tracked  :", len(run("git ls-files", P01, 120).splitlines()))
    print("  status   :")
    for line in run("git status --porcelain", P01, 120).splitlines()[:20]:
        print("     " + line)
    print("  log      :")
    for line in run("git log --oneline --decorate -8", P01, 120).splitlines():
        print("     " + line)


# --------------------------------------------------------------------------
# recon3 的能力
# --------------------------------------------------------------------------

def cmd_disk_links():
    """全盘 junction 定位（起点 C:\\Users\\<user>，跳过 JVS 与系统目录）。"""
    print(SEP)
    print("1. 全盘定位 junction（起点: C:\\Users\\<user>，跳过 JVS 与系统目录）")
    print(SEP)
    bases = [r"C:\Users\<user>\host-workspace", r"C:\Users\<user>\Desktop"]
    jvs_real = os.path.realpath(JVS).lower()
    found = []
    for base in bases:
        for root, dirs, files in os.walk(base):
            if os.path.realpath(root).lower().startswith(jvs_real):
                dirs[:] = []
                continue
            for d in list(dirs):
                p = os.path.join(root, d)
                if is_reparse(p):
                    found.append(p)
                    dirs.remove(d)
            for f in files:
                p = os.path.join(root, f)
                if is_reparse(p):
                    found.append(p + "  [FILE]")
    print("  命中 %d 个：" % len(found))
    for p in found:
        real = os.path.realpath(p)
        inside = "JVS内" if real.lower().startswith(jvs_real) else "!! JVS外 !!"
        print("   %s" % p)
        print("        -> %s   [%s]" % (real, inside))


def cmd_ws_config():
    """JVS\\.workbuddy-ai 内容清单。"""
    print(SEP)
    print("2. JVS\\.workbuddy-ai 内容")
    print(SEP)
    for root, dirs, files in os.walk(os.path.join(JVS, ".workbuddy-ai")):
        for f in files:
            p = os.path.join(root, f)
            print("  %8d B  %s" % (os.path.getsize(p), os.path.relpath(p, JVS)))


def cmd_longest_path(threshold=240, top=10):
    """最长路径 / 是否超 Windows MAX_PATH。"""
    print(SEP)
    print("3. 最长路径 / 是否超出 Windows MAX_PATH")
    print(SEP)
    longest = []
    n_over = 0
    for root, dirs, files in os.walk(JVS):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for f in files:
            p = os.path.join(root, f)
            L = len(p)
            if L > threshold:
                n_over += 1
            longest.append((L, p))
    longest.sort(reverse=True)
    for L, p in longest[:top]:
        print("  len=%4d  %s" % (L, os.path.relpath(p, JVS)))
    print("  超过 %d 字符的路径数: %d" % (threshold, n_over))
    print("  长度上限: %d" % longest[0][0])


def cmd_dup(root_name, skip_ext=(".sse", ".zip"), top=8, cache=None):
    """内容重复率（同内容不同路径）。

    ⚠️ 原 `git_recon3.py` 把**同一段代码抄了两遍**（§4 对 02 分区、§5 对 03 分区），
    本子命令用 `--root` 参数化，是本次合并**唯一真正消掉重复**的地方。
    ⚠️ 但两节并非逐字相同：**§5 漏了末尾的 Top 明细循环**。`--top 0` 复现 §5，
    `--top 8`（默认）复现 §4。详见模块头「合并口径」。

    `cache` 为 `HashCache` 实例或 `None`。**None ⇒ 与合并前逐字一致**（只换了分块读）。
    """
    base = root_name if os.path.isabs(root_name) else os.path.join(JVS, root_name)
    print(SEP)
    print("%s 分区内容重复率（同内容不同路径）" % root_name)
    print(SEP)
    hashes = {}
    tot_n = tot_b = 0
    skipped = 0
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for f in files:
            if os.path.splitext(f)[1].lower() in skip_ext:
                continue
            p = os.path.join(root, f)
            try:
                sz = os.path.getsize(p)
                h = cache.sha256(p) if cache is not None else file_sha256(p)
            except Exception:
                skipped += 1
                continue
            tot_n += 1
            tot_b += sz
            d = hashes.setdefault(h, [0, sz])
            d[0] += 1
    dup_files = sum(v[0] - 1 for v in hashes.values() if v[0] > 1)
    dup_bytes = sum((v[0] - 1) * v[1] for v in hashes.values() if v[0] > 1)
    print("  文件总数 %d  体积 %.2f MB" % (tot_n, tot_b / 1048576))
    print("  唯一内容 %d" % len(hashes))
    print("  冗余文件 %d (%.1f%%)  冗余体积 %.2f MB (%.1f%%)"
          % (dup_files, dup_files * 100.0 / tot_n,
             dup_bytes / 1048576, dup_bytes * 100.0 / tot_b))
    top_items = sorted(hashes.items(), key=lambda kv: -(kv[1][0] - 1) * kv[1][1])[:top]
    for h, (n, sz) in top_items:
        print("   x%-3d %9.0f KB  冗余 %6.2f MB  %s"
              % (n, sz / 1024, (n - 1) * sz / 1048576, h[:12]))
    if cache is not None:
        # ⚠️ 诊断信息走 **stderr**：这样 stdout 的「报告」在 `--cache` 与 `--no-cache`
        #    下**逐字一致**，A/B 等价性可以直接比 stdout，不需要任何白名单。
        print("[cache] 命中 %d  未命中 %d  命中率 %.1f%%  条目 %d"
              % (cache.hits, cache.misses, cache.hit_rate(), len(cache.entries)),
              file=sys.stderr)
        if skipped:
            print("[cache] 读取失败跳过 %d 个（与 --no-cache 同口径：失败即跳过）" % skipped,
                  file=sys.stderr)
        cache.save()


CASES = {
    "links": ("JVS 内部 junction / 符号链接", cmd_links, "recon1 §1"),
    "cred": ("凭证 / 会话文件名扫描", cmd_cred, "recon1 §2"),
    "secrets": ("文本秘密模式扫描", cmd_secrets, "recon1 §3"),
    "restricted": ("04 受限素材盘点", cmd_restricted, "recon1 §4"),
    "exclude-sizing": ("可提交体积估算", cmd_exclude_sizing, "recon1 §5"),
    "git-capability": ("git 工具能力", cmd_git_capability, "recon1 §6"),
    "ws-links": ("旧工作区 junction 列表", cmd_ws_links, "recon2 §1"),
    "nested-git": ("JVS 内所有 .git", cmd_nested_git, "recon2 §2"),
    "images-big": ("图片 / 大文件分布", cmd_images_big, "recon2 §3"),
    "repo01": ("01 仓库状态", cmd_repo01, "recon2 §4"),
    "disk-links": ("全盘 junction 定位", cmd_disk_links, "recon3 §1"),
    "ws-config": (".workbuddy-ai 内容", cmd_ws_config, "recon3 §2"),
    "longest-path": ("最长路径", cmd_longest_path, "recon3 §3"),
    "dup": ("内容重复率（--root）", None, "recon3 §4+§5"),
}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="JVS 仓库侦察（合并自 git_recon{,2,3}.py）")
    ap.add_argument("cmd", nargs="?", help="侦察项名（见 --list）")
    ap.add_argument("--root", default="02-m1-evaluation",
                    help="dup 的目标分区（相对 JVS 或绝对路径）；默认 02-m1-evaluation")
    ap.add_argument("--top", type=int, default=8,
                    help="dup 的「Top N 冗余内容」行数；0 = 不列（复现原 recon3 §5）；默认 8")
    ap.add_argument("--cache", metavar="PATH", default=None,
                    help="dup 的哈希缓存文件（开启记忆化；默认关闭）")
    ap.add_argument("--no-cache", action="store_true",
                    help="显式关闭缓存（默认行为；用于在脚本里写死口径）")
    ap.add_argument("--list", action="store_true", help="列出全部子命令")
    args = ap.parse_args(argv)

    if args.list or not args.cmd:
        print("可用侦察项（共 %d 个）：" % len(CASES))
        for name, (title, _fn, origin) in CASES.items():
            print("  %-16s %-30s ← %s" % (name, title, origin))
        return 0 if args.list else 3

    if args.cmd not in CASES:
        print("未知侦察项：%s（用 --list 查看）" % args.cmd, file=sys.stderr)
        return 3

    if args.cmd == "dup":
        if args.cache and args.no_cache:
            print("--cache 与 --no-cache 不能同时给", file=sys.stderr)
            return 3
        hc = None
        if args.cache and not args.no_cache:
            hc = HashCache(args.cache)
        cmd_dup(args.root, top=args.top, cache=hc)
    else:
        CASES[args.cmd][1]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
