# -*- coding: utf-8 -*-
r"""路径 → SHA256 记忆化缓存（带显式失效与自证判据）。

为什么需要
----------
本仓库多处要「遍历数 GB、逐文件算 sha256」，同一次调查里反复重跑就把同样的
字节重算一遍。实测代价：`repo_recon.py dup --root 02-m1-evaluation` 与
`--root 03-d10-workspace` 各要全量哈希一个分区（阶段 B 的合并验证里连跑了 3 轮），
而 `03-d10-workspace` 恰恰是**文件数瓶颈**分区（13k 文件 / 中位数 0.9 KB /
吞吐 25 MB/s）—— 瓶颈在 `open/close`，不在字节，**缓存正好打在瓶颈上**。

调用方
------
本模块是**库**（`from hash_cache import HashCache, file_sha256`）+ 自带 CLI。

| 调用方 | 状态 | 说明 |
|---|---|---|
| `repo_recon.py dup` | **已接入（opt-in）** | `--cache PATH` 开启；**默认关闭**，不加参数时行为与合并前完全一致 |
| `make_fingerprints.py` | **刻意不接入** | 它一次只算 13 个文件、且是「生成冻结指纹」的校验语义，收益≈0、风险≠0 |

⚠️ 为什么 `dup` 用 **opt-in** 而不是默认开：`dup` 是**报告 / 盘点**语义
（结果只用于统计），但默认开启会让「同一命令两次跑出不同耗时/不同缓存状态」，
给 A/B 等价性验证引入噪声。**默认关闭 ⇒ 旧行为逐字可复现**，缓存是显式加速器。

⚠️ 已知过期面 —— **必须知道，否则会把缓存用错地方**
--------------------------------------------------
缓存键 = `(绝对路径, size, mtime_ns)`。因此：

1. **size 与 mtime_ns 都没变、内容却变了** ⇒ 缓存返回**错的**哈希。
   这是真实可构造的：`git restore` / 解压覆盖 / 保留 mtime 的写回工具。
   缓解：`--invalidate PATH` 显式失效；本模块**不**声称能自动发现这一类。
2. 某些文件系统（网络盘、部分虚拟盘）`mtime_ns` 精度只到秒 ⇒ 同一秒内的改写漏判。
3. 大小写不敏感的文件系统上 `A.py` 与 `a.py` 是同一文件：本缓存按字面路径存键，
   会存两条，但两条的值相同，不影响正确性（只多占一点空间）。
4. 缓存文件本身若被并发写坏：写盘走「临时文件 + `os.replace`」原子替换，
   读盘解析失败时**整体弃用并重建**（不会带着半截缓存继续用）。

⇒ **凡「校验」语义（必须真读字节以确认未被篡改）的调用方，一律不得接入本缓存。**
   本项目的典型禁区，已实测确认「重复读」是刻意的：

   - `scripts/git-gate.sh` 第 `[4]` 步的冻结指纹复核（每次提交都要跑；实测 13 项约 1.4 s，
     本来就不慢 —— **缓存化会把它从「证明字节没变」降级成「证明缓存没变」**）
   - `03-d10-workspace/2026-09-08-20-30-19/outputs/verify_delivery.py`
   - `03-d10-workspace/2026-09-08-20-30-19/outputs/compare_models.py`

   它们读的不是「为了算个统计量」，而是「为了证明字节没变」。缓存一旦介入，
   校验就退化为「证明缓存没变」，正是它要防的那件事。

自证判据
--------
    python _migrate/hash_cache.py --verify --sample 20

从缓存里**随机抽 N 条**，**重新从磁盘算** sha256 与缓存值比对，要求 **不符 0 条**。
这是本模块唯一的正确性证据来源；任何调用方在首次使用前都应先跑一次。
抽样用**固定种子**（`random.Random(20260920)`）⇒ 可复跑、结果可比。

命中率
------
    python _migrate/hash_cache.py --bench 02-m1-evaluation

对同一批文件连算两遍，打印第 2 遍的命中率（应当 ≈ 100%）。
`--stats` 看缓存条目数、按前缀分布、文件体积。

缓存位置
--------
默认 `%TEMP%\jvs-hash-cache.json`（**刻意放在仓库之外**）：
放进仓库会多出一个未跟踪产物，而本项目的索引层集合差校验会把「非归档新增」判为异常。
可用 `--cache PATH` 覆盖。
"""
import argparse
import hashlib
import json
import os
import random
import sys
import tempfile

# ⚠️ 由 __file__ 推导仓根，**不硬编码绝对路径**
#    （`_migrate/xxx.py` 的上一级即仓根；硬编码会在 linked worktree / 换机时静默写错位置，
#      即索引技能坑 11 的同类问题）
JVS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCHEMA = "jvs-hash-cache/1"
CHUNK = 1 << 20          # 1 MiB
DEFAULT_CACHE = os.path.join(tempfile.gettempdir(), "jvs-hash-cache.json")


def file_sha256(p, chunk=CHUNK):
    """分块读并算 sha256（不把整个文件读进内存）。

    与 `hashlib.sha256(open(p,'rb').read())` **结果逐位相同** ——
    sha256 是流式算法，分块喂入不影响摘要。区别只在峰值内存。
    """
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class HashCache:
    """`(path, size, mtime_ns)` → sha256 的记忆化表。

    典型用法：
        c = HashCache()                 # 默认路径
        h = c.sha256(path)              # 命中则零 I/O，未命中则算并记
        c.save()                        # 显式落盘（不自动保存，避免半途写坏）
    """

    def __init__(self, path=None, enabled=True):
        self.path = path or DEFAULT_CACHE
        self.enabled = enabled
        self.entries = {}       # abspath -> [size, mtime_ns, sha256]
        self.hits = 0
        self.misses = 0
        self.dirty = False
        if enabled:
            self._load()

    # ---------------- 持久化 ----------------

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            if d.get("schema") != SCHEMA:
                return                      # 版本不符 -> 弃用重建
            e = d.get("entries") or {}
            if isinstance(e, dict):
                self.entries = e
        except FileNotFoundError:
            return
        except Exception:
            # 缓存损坏不是错误：整体弃用并重建，绝不用半截缓存
            self.entries = {}

    def save(self):
        if not self.enabled or not self.dirty:
            return False
        d = {"schema": SCHEMA, "entries": self.entries}
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path) or ".",
                                   prefix=".hashcache-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(d, fh, ensure_ascii=False)
            os.replace(tmp, self.path)      # 原子替换
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self.dirty = False
        return True

    # ---------------- 主接口 ----------------

    def sha256(self, p):
        """返回 sha256 十六进制串。文件不存在 / 不可读则抛异常（与直接算一致）。"""
        ap = os.path.abspath(p)
        st = os.stat(ap)
        size, mt = st.st_size, st.st_mtime_ns
        if self.enabled:
            hit = self.entries.get(ap)
            if hit and hit[0] == size and hit[1] == mt:
                self.hits += 1
                return hit[2]
        self.misses += 1
        h = file_sha256(ap)
        if self.enabled:
            self.entries[ap] = [size, mt, h]
            self.dirty = True
        return h

    def invalidate(self, p):
        """显式失效一条（应对「size/mtime 未变但内容变了」）。"""
        ap = os.path.abspath(p)
        if ap in self.entries:
            del self.entries[ap]
            self.dirty = True
            return True
        return False

    def clear(self):
        n = len(self.entries)
        self.entries = {}
        self.dirty = True
        return n

    def hit_rate(self):
        tot = self.hits + self.misses
        return (self.hits * 100.0 / tot) if tot else 0.0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _walk_files(root, skip_ext=(".sse", ".zip")):
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in (".git", "__pycache__")]
        for f in fn:
            if os.path.splitext(f)[1].lower() in skip_ext:
                continue
            yield os.path.join(dp, f)


def _group_key(ap):
    """分组键：仓内文件取**相对 `JVS_ROOT` 的首段**；仓外的按「盘符 + 首两段」归类。

    ⚠️ 不能按「相对盘符根」取首段 —— 那会把本仓所有条目都归成一个 `Users`
    （`C:\\Users\\<user>\\Desktop\\JVS\\...` 的首段就是 `Users`），`--stats` 完全失去信息量。
    """
    try:
        r = os.path.relpath(ap, JVS_ROOT)
    except ValueError:              # 不同盘符
        r = None
    if r and not r.startswith(".."):
        return r.split(os.sep)[0]
    parts = os.path.normpath(ap).split(os.sep)
    return os.sep.join(parts[:3]) if len(parts) >= 3 else ap


def cmd_stats(c):
    print("缓存文件: %s" % c.path)
    print("  存在: %s" % os.path.exists(c.path))
    if os.path.exists(c.path):
        print("  体积: %.2f MB" % (os.path.getsize(c.path) / 1048576))
    print("  条目: %d" % len(c.entries))
    pre = {}
    for ap in c.entries:
        k = _group_key(ap)
        pre[k] = pre.get(k, 0) + 1
    for k in sorted(pre, key=lambda x: -pre[x])[:12]:
        print("    %-34s %5d" % (k, pre[k]))
    return 0


def cmd_verify(c, sample):
    n = len(c.entries)
    if n == 0:
        print("缓存为空，无可验证条目（先跑一次 --bench 或接入的调用方）")
        return 3
    keys = sorted(c.entries)
    if sample and sample < n:
        keys = random.Random(20260920).sample(keys, sample)   # 固定种子 -> 可复跑
    bad = miss = good = 0
    for ap in keys:
        if not os.path.exists(ap):
            print("  [SKIP] 已不存在 %s" % ap)
            miss += 1
            continue
        st = os.stat(ap)
        size, mt, cached = c.entries[ap]
        fresh = file_sha256(ap)
        if fresh != cached:
            print("  [FAIL] %s" % ap)
            print("         缓存 %s" % cached[:32])
            print("         现算 %s" % fresh[:32])
            print("         size 缓存/现测 %s/%s  mtime_ns 缓存/现测 %s/%s"
                  % (size, st.st_size, mt, st.st_mtime_ns))
            bad += 1
        else:
            good += 1
    print("  抽验 %d / %d 条：一致 %d，不符 %d，已不存在 %d"
          % (len(keys), n, good, bad, miss))
    print("★ 自证通过（不符 0 条）" if not bad else "✘ 自证失败")
    return 0 if not bad else 1


def cmd_bench(c, root_name, repeat=2):
    base = root_name if os.path.isabs(root_name) else os.path.join(JVS_ROOT, root_name)
    files = list(_walk_files(base))
    print("基准目录: %s" % base)
    print("  待哈希文件 %d 个" % len(files))
    for i in range(1, repeat + 1):
        c.hits = c.misses = 0
        tot = 0
        for p in files:
            try:
                c.sha256(p)
            except Exception:
                continue
            tot += 1
        print("  第 %d 遍: %d 个文件  命中 %d  未命中 %d  命中率 %.1f%%"
              % (i, tot, c.hits, c.misses, c.hit_rate()))
    c.save()
    print("  已保存缓存: %s（%d 条）" % (c.path, len(c.entries)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="路径 -> SHA256 记忆化缓存")
    ap.add_argument("--cache", default=DEFAULT_CACHE, help="缓存文件路径")
    ap.add_argument("--stats", action="store_true", help="显示缓存统计")
    ap.add_argument("--verify", action="store_true", help="自证：抽样重算比对")
    ap.add_argument("--sample", type=int, default=20, help="--verify 抽样条数（0=全量）")
    ap.add_argument("--bench", metavar="ROOT", help="对 ROOT 连算两遍，测命中率")
    ap.add_argument("--invalidate", metavar="PATH", action="append", default=[],
                    help="显式失效某条（可重复）")
    ap.add_argument("--clear", action="store_true", help="清空全部条目")
    args = ap.parse_args(argv)

    c = HashCache(args.cache)
    did = False

    if args.invalidate:
        for p in args.invalidate:
            print("失效 %s : %s" % ("OK" if c.invalidate(p) else "未收录", p))
        c.save()
        did = True
    if args.clear:
        print("已清空 %d 条" % c.clear())
        c.save()
        did = True
    if args.bench:
        cmd_bench(c, args.bench)
        did = True
    if args.verify:
        rc = cmd_verify(c, args.sample)
        did = True
        if rc:
            return rc
    if args.stats or not did:
        cmd_stats(c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
