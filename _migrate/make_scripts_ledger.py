# -*- coding: utf-8 -*-
"""生成「一次性脚本登记表」：把脚本与带生命周期标记的文件按状态分桶。

对应 `REFACTOR-PLAN.md` §三 R5：不搬、不删，只补一份**登记表**，收益是认知负担下降。
本表**由生成器产出，随重跑刷新** —— 手工编辑产物会被下一次重跑抹掉（见技能
`jvs-index-layer-maintenance` 坑 1）。

**进表范围**（这是本生成器最容易写错的一处，务必看清）：

| 进表 | 判据 |
|---|---|
| 全部 `.py` | 脚本本体 |
| 带生命周期标记的**任意**文件 | 名称含 `superseded` / `.bak-` / `.bak.` / 结尾 `.bak` / `.orig` / `.rej` |

⚠️ **为什么不能只写 `f.endswith(".py")`**：Python 备份文件的形态是 `x.py.bak-20260915-r8`，
**它不以 `.py` 结尾** —— 2026-09-20 实测该写法漏掉 **16 个 `.py.bak-*`**（可弃桶从应有的量级掉到 10）。
`REFACTOR-PLAN.md` §三 R5 的「`.bak-*` 27 个」同样有口径问题：现测**全库 `.bak-*` = 25 个**
（与 `ORGANIZATION-AUDIT.md` 记的 25 一致 ⇒ 方案里那个 27 才是漂移值），
而「`superseded` 77」是**文件 45 + 目录 32** 的合计 ⇒ 本表分列两者，不合并成一个数。

分桶口径（**三种，互斥**）：

| 桶 | 判据 | 含义 |
|---|---|---|
| `可弃` | 名称含生命周期标记 | 已被取代或补丁残留，**可退役**（退役需另行授权，本表只登记） |
| `一次性` | `.py` 且基名以 `_` 开头（项目约定） | 一次性/探测/中间脚本，非稳定入口 |
| `活件` | 其余 `.py` | 有明确入口或仍被引用 |

输出：`_index/一次性脚本登记表.md`
自检：三桶求和 == 进表总数（不成立即报错退出，防「检查工具自身过期」）。

⚠️ 路径根**从 `__file__` 推导**，不硬编码主工作树 —— 见技能坑 11
（生成器硬编码主工作树路径 ⇒ 在 linked worktree 里跑会写到主工作树）。
"""
import io
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_index", "一次性脚本登记表.md")

# 不进登记表：版本库内部、缓存、工作区记忆、受限素材（独立仓库，不外枚举）
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".workbuddy-ai", "04-restricted-materials"}

BUCKETS = ("活件", "一次性", "可弃")
LIFECYCLE = re.compile(r"superseded|\.bak-|\.bak\.|\.bak$|\.orig$|\.rej$", re.I)


def included(name):
    return name.endswith(".py") or bool(LIFECYCLE.search(name))


def bucket_of(name):
    if LIFECYCLE.search(name):
        return "可弃"
    if name.startswith("_"):
        return "一次性"
    return "活件"


def tracked_set():
    """一次问 git 要全量跟踪清单（逐文件问会慢几百次）。"""
    try:
        r = subprocess.run(["git", "-C", ROOT, "ls-files", "-z"],
                           capture_output=True, timeout=120)
        return {x.replace("/", "\\") for x in r.stdout.decode("utf-8", "replace").split("\0") if x}
    except Exception:
        return set()


def scan():
    rows, sup_dirs = [], []
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if not os.path.islink(os.path.join(dp, d)) and d not in SKIP_DIRS]
        for d in dn:
            if LIFECYCLE.search(d):
                sup_dirs.append(os.path.relpath(os.path.join(dp, d), ROOT).replace("\\", "/"))
        for f in fn:
            if not included(f):
                continue
            rel = os.path.relpath(os.path.join(dp, f), ROOT).replace("\\", "/")
            rows.append((rel, bucket_of(f)))
    return sorted(rows), sorted(sup_dirs)


def main():
    rows, sup_dirs = scan()
    total = len(rows)
    counts = {b: 0 for b in BUCKETS}
    for _, b in rows:
        counts[b] += 1
    if sum(counts.values()) != total:
        raise SystemExit("自检失败：分桶求和 %d != 进表总数 %d" % (sum(counts.values()), total))

    n_py = sum(1 for r, _ in rows if r.endswith(".py"))
    tracked = tracked_set()

    def mk(rel):
        return "✅" if rel.replace("/", "\\") in tracked else "⚠️未入库"

    by_dir = {}
    for rel, b in rows:
        d = os.path.dirname(rel) or "."
        by_dir.setdefault(d, {x: 0 for x in BUCKETS})[b] += 1

    L = []
    A = L.append
    A("# 一次性脚本登记表 —— JVS / M1")
    A("")
    A("> **自动生成，请勿手工编辑** —— 生成器 `_migrate/make_scripts_ledger.py`，")
    A("> 重跑 `python -B _migrate/make_scripts_ledger.py` 即刷新。")
    A("> 生成时间：%s ｜ 依据：`REFACTOR-PLAN.md` §三 R5" % time.strftime("%Y-%m-%d %H:%M"))
    A(">")
    A("> 本表**只登记、不搬不删**。退役任何一项都需另行授权。")
    A("")
    A("---")
    A("")
    A("## 一、分桶汇总")
    A("")
    A("| 桶 | 判据 | 数量 | 占比 |")
    A("|---|---|---:|---:|")
    for b, rule in (("活件", "其余 `.py`（有明确入口或仍被引用）"),
                    ("一次性", "`.py` 且基名以 `_` 开头（项目约定）"),
                    ("可弃", "名称含 `superseded` / `.bak-*` / `.bak.` / `.orig` / `.rej`")):
        A("| `%s` | %s | %d | %.1f%% |" % (b, rule, counts[b], 100.0 * counts[b] / total))
    A("| **合计（进表）** | — | **%d** | 100%% |" % total)
    A("")
    A("进表范围：全部 `.py`（%d 个）**加上**带生命周期标记的任意文件。口径说明见生成器 docstring ——"
      % n_py)
    A("⚠️ **只写 `endswith('.py')` 会漏掉 `x.py.bak-*` 这类备份**（它不以 `.py` 结尾）。")
    A("排除：`.git` / `__pycache__` / `node_modules` / `.workbuddy-ai` / `04-restricted-materials/`，"
      "且**不跟随 junction**。")
    A("")
    A("**另计**：名称含 `superseded` 的**目录** %d 个（登记为已被取代的快照目录，未搬未删）。"
      % len(sup_dirs))
    A("")
    A("> ⚠️ **勿把本表数字引用到别处** —— 数字随重跑变化。需要时**现测**：")
    A("> `python -B _migrate/make_scripts_ledger.py`。")
    A("")
    A("---")
    A("")
    A("## 二、按目录分布")
    A("")
    A("| 目录 | 活件 | 一次性 | 可弃 | 小计 |")
    A("|---|---:|---:|---:|---:|")
    for d in sorted(by_dir):
        c = by_dir[d]
        A("| `%s/` | %d | %d | %d | %d |"
          % (d, c["活件"], c["一次性"], c["可弃"], sum(c.values())))
    A("")
    A("---")
    A("")

    def dump(title, want):
        A("## %s" % title)
        A("")
        sub = [(r, b) for r, b in rows if b == want]
        A("共 **%d** 个。" % len(sub))
        cur = None
        for rel, _ in sub:
            d = os.path.dirname(rel) or "."
            if d != cur:
                cur = d
                A("")
                A("**`%s/`**" % d)
                A("")
            A("- `%s` %s" % (os.path.basename(rel), mk(rel)))
        A("")
        A("---")
        A("")

    dump("三、可弃清单（登记为可退役，**未退役**）", "可弃")
    dump("四、一次性清单（`.py` 且下划线前缀）", "一次性")

    A("## 五、活件清单")
    A("")
    A("共 **%d** 个。它们是「该跑哪个脚本」的候选池；带入口说明的精选表见 "
      "`_handoff/scripts/SCRIPTS.md`。" % counts["活件"])
    A("")
    cur = None
    for rel, b in rows:
        if b != "活件":
            continue
        d = os.path.dirname(rel) or "."
        if d != cur:
            cur = d
            A("")
            A("**`%s/`**" % d)
            A("")
        A("- `%s` %s" % (os.path.basename(rel), mk(rel)))
    A("")
    A("---")
    A("")
    A("## 六、已被取代的快照目录（`superseded`，**未搬未删**）")
    A("")
    A("共 **%d** 个。它们是历史轮次的留档目录（内容被更新的轮次取代），不是空壳。" % len(sup_dirs))
    A("")
    for d in sup_dirs:
        A("- `%s/`" % d)
    A("")
    A("---")
    A("")
    A("## 七、自检")
    A("")
    A("- 分桶求和 `%d` == 进表总数 `%d` ⇒ **成立**" % (sum(counts.values()), total))
    A("- `⚠️未入库` 标记表示该文件**在本机存在但未被 git 跟踪** —— 克隆副本上不会有它；")
    A("  若它是入口脚本，接手者会直接失败（2026-09-20 `_handoff/env/` 已踩过一次）。")
    A("")
    A("重测命令（**数字一律现测，勿引用本表**）：")
    A("")
    A("```bash")
    A("cd /c/Users/<user>/jvs-src")
    A('PY="C:/Users/<user>/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe"')
    A('"$PY" -B _migrate/make_scripts_ledger.py    # 重跑并刷新本表')
    A("```")
    A("")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print("进表 %d ｜ 活件 %d / 一次性 %d / 可弃 %d ｜ superseded 目录 %d"
          % (total, counts["活件"], counts["一次性"], counts["可弃"], len(sup_dirs)))
    print("已写出: %s  (%d bytes)" % (OUT, os.path.getsize(OUT)))
    return 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
