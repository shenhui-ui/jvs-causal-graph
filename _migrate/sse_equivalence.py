# -*- coding: utf-8 -*-
r""".sse 与 response.json 一致性 / .sse 体积特性的统一入口。

合并自 `verify_sse_equivalence.py` 与 `verify_sse_equivalence2.py`。
两者**共享同一段骨架**（`sse_reasoning()` 逐行解析 `data: {...}` 分片、
`^<base>\.payload\.stream-response-<hex>\.sse$` 配对正则、response.json 取
`choices[0].message.reasoning_content/content`），差异只在**报告口径**：

| 子命令 | 原脚本 | 口径 |
|---|---|---|
| `compare`  | `verify_sse_equivalence.py` §1 | 3 个固定 BASE、非递归、**逐条精确比对**（默认 6 组），不一致时打印首尾片段 |
| `uniq`     | `verify_sse_equivalence.py` §2 | 按 sha256 统计 `.sse` 唯一内容占比（回答「纳入仓库的实际体积」） |
| `compress` | `verify_sse_equivalence.py` §3 | zlib 抽样压缩率估算（默认 30 个） |
| `sample`   | `verify_sse_equivalence2.py`   | **全库递归**大样本分桶统计（默认 400 组），回答「排除 `.sse` 后思考链会不会断」 |

用法：
    python _migrate/sse_equivalence.py compare
    python _migrate/sse_equivalence.py sample --limit 400
    python _migrate/sse_equivalence.py uniq
    python _migrate/sse_equivalence.py compress --limit 30
    python _migrate/sse_equivalence.py all      # compare + uniq + compress（等价于旧 v1 全文）

⚠️ 只读；`uniq` / `compress` 会遍历数 GB 的 `.sse`，耗时较长。
"""
import argparse
import collections
import hashlib
import json
import os
import re
import sys
import zlib

JVS = r"C:\Users\<user>\Desktop\JVS"

# `^<base>.payload.stream-response-<hex>.sse$` —— 两个原脚本逐字相同的配对正则
PAIR_RE = re.compile(r"^(.*?)\.payload\.stream-response-[0-9a-f]+\.sse$")

# 旧 v1 的 3 个固定 BASE（非递归 os.listdir）
DEFAULT_BASES = [
    os.path.join(JVS, r"02-m1-evaluation\outputs\m1-10-d10-event-extract-full-20260908\delivery-pro-r1\results"),
    os.path.join(JVS, r"02-m1-evaluation\outputs\m1-10-d10-event-extract-pilot-20260908\results"),
    os.path.join(JVS, r"03-d10-workspace\2026-09-08-20-30-19\outputs\full-review-full-20260909\evidence"),
]

# 旧 v1 §3 硬编码的「全库 .sse 总量」（MB）。⚠️ 这是**会静默过期的枚举值**：
# 2026-09-16 实测值。要重新使用请先现测（`git ls-files -z "*.sse" | xargs -0 du -cb`），
# 不要直接引用。
LEGACY_TOTAL_SSE_MB = 3511

SKIP_DIRS = {".git", "__pycache__"}


def sse_reasoning(path):
    """从 .sse 逐行解析 `data: {...}` 分片，按序拼接 delta.reasoning_content 与 delta.content。"""
    reason, content = [], []
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload in ("[DONE]", ""):
                continue
            try:
                o = json.loads(payload)
            except Exception:
                continue
            ch = (o.get("choices") or [{}])[0]
            d = ch.get("delta") or {}
            r = d.get("reasoning_content")
            c = d.get("content")
            if r:
                reason.append(r)
            if c:
                content.append(c)
    return "".join(reason), "".join(content)


def response_pair(sse_path, base_name):
    """返回配对 response.json 的路径（不存在则 None）。"""
    m = PAIR_RE.match(base_name)
    if not m:
        return None
    return os.path.join(os.path.dirname(sse_path),
                        m.group(1) + ".payload.response.json")


def read_response(rj):
    """取 (reasoning_content, content)；解析失败返回 None。"""
    try:
        d = json.load(open(rj, encoding="utf-8"))
        msg = d["choices"][0].get("message") or {}
        return (msg.get("reasoning_content") or ""), (msg.get("content") or "")
    except Exception:
        return None


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------

def cmd_compare(bases, limit):
    """逐条精确比对（旧 v1 §1）。"""
    print("=" * 74)
    print("决定性验证：.sse 拼接 vs response.json（思考链一致性）")
    print("=" * 74)
    tested = 0
    for base in bases:
        if not os.path.isdir(base):
            continue
        for fn in sorted(os.listdir(base)):
            if not fn.endswith(".sse"):
                continue
            p = os.path.join(base, fn)
            m = PAIR_RE.match(fn)
            if not m:
                continue
            rj = os.path.join(base, m.group(1) + ".payload.response.json")
            if not os.path.exists(rj):
                continue
            try:
                sr, sc = sse_reasoning(p)
            except Exception as e:
                print(f"  !! .sse 解析失败 {fn}: {e}")
                continue
            pair = read_response(rj)
            if pair is None:
                print(f"  !! response.json 解析失败 {m.group(1)}")
                continue
            rr, rc = pair
            tested += 1
            eq_r = (sr == rr)
            eq_c = (sc == rc)
            print(f"  {m.group(1)}")
            print(f"      `思考链` .sse {len(sr):>7} 字符 | response.json {len(rr):>7} 字符 | 完全一致={eq_r}")
            print(f"      `正文`   .sse {len(sc):>7} 字符 | response.json {len(rc):>7} 字符 | 完全一致={eq_c}")
            if not eq_r and rr:
                print(f"         sse头: {repr(sr[:80])}")
                print(f"        resp头: {repr(rr[:80])}")
                print(f"         sse尾: {repr(sr[-80:])}")
                print(f"        resp尾: {repr(rr[-80:])}")
            if tested >= limit:
                break
        if tested >= limit:
            break
    print(f"\n  已精确比对 {tested} 组")
    return tested


def cmd_sample(root, limit):
    """全库递归大样本分桶统计（旧 v2）。"""
    buckets = collections.OrderedDict([
        ("完全一致(思考链+正文)", 0), ("仅正文一致", 0), ("都不一致", 0),
        ("response缺思考链", 0), ("解析失败", 0),
    ])
    inconsistent = []
    tested = 0

    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if not fn.endswith(".sse"):
                continue
            m = PAIR_RE.match(fn)
            if not m:
                continue
            p = os.path.join(r, fn)
            rp = os.path.join(r, m.group(1) + ".payload.response.json")
            if not os.path.exists(rp):
                continue
            try:
                sr, sc = sse_reasoning(p)
            except Exception:
                buckets["解析失败"] += 1
                continue
            pair = read_response(rp)
            if pair is None:
                buckets["解析失败"] += 1
                continue
            rr, rc = pair
            tested += 1
            if not rr:
                buckets["response缺思考链"] += 1
                if sr:
                    inconsistent.append((os.path.relpath(p, root),
                                         "response无思考链但sse有", len(sr), 0))
            elif sr == rr and sc == rc:
                buckets["完全一致(思考链+正文)"] += 1
            elif sc == rc:
                buckets["仅正文一致"] += 1
                inconsistent.append((os.path.relpath(p, root), "仅正文一致", len(sr), len(rr)))
            else:
                buckets["都不一致"] += 1
                inconsistent.append((os.path.relpath(p, root), "都不一致", len(sr), len(rr)))
            if tested >= limit:
                break
        if tested >= limit:
            break

    print("=" * 74)
    print(f"同目录配对统计（样本 {tested} 组）")
    print("=" * 74)
    for k in buckets:
        v = buckets[k]
        print(f"  {k:<24} {v:>5}  ({v*100.0/max(tested,1):>5.1f}%)")
    print()
    print(f"  不一致明细（{len(inconsistent)} 条，最多列 20）:")
    for rel_, kind, a, b in inconsistent[:20]:
        print(f"    [{kind}] sse={a} resp={b}")
        print(f"       {rel_}")
    print()
    print("=" * 74)
    print("结论摘要")
    print("=" * 74)
    ok = buckets["完全一致(思考链+正文)"]
    print(f"  .sse 与 response.json 完全一致的占比: {ok*100.0/max(tested,1):.1f}%")
    print(f"  -> 思考链（reasoning_content）已由 response.json 完整承载"
          f"{'（逐字符一致）' if ok == tested else ''}")
    return tested


def cmd_uniq(roots):
    """`.sse` 唯一内容占比（旧 v1 §2）。roots 为 (tag, path) 序列。"""
    print()
    print("=" * 74)
    print("补充：.sse 唯一内容占比（决定若纳入仓库的实际体积）")
    print("=" * 74)
    for tag, base in roots:
        hs = {}
        n = b = 0
        for r, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                if not f.endswith(".sse"):
                    continue
                p = os.path.join(r, f)
                try:
                    sz = os.path.getsize(p)
                    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
                except Exception:
                    continue
                n += 1
                b += sz
                hs[h] = hs.get(h, 0) + 1
        uniq = len(hs)
        dupf = sum(v - 1 for v in hs.values() if v > 1)
        print(f"  {tag}: .sse {n} 个 / {b/1048576:.1f} MB；唯一内容 {uniq} 个；"
              f"重复 {dupf} 个 ({dupf*100.0/max(n,1):.1f}%)")


def cmd_compress(root, limit):
    """zlib 抽样压缩率估算（旧 v1 §3）。"""
    print()
    print("=" * 74)
    print("补充：.sse 文本压缩率估算（zlib，抽样 %d 个）" % limit)
    print("=" * 74)
    tot = comp = 0
    cnt = 0
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if not f.endswith(".sse"):
                continue
            p = os.path.join(r, f)
            try:
                raw = open(p, "rb").read()
            except Exception:
                continue
            tot += len(raw)
            comp += len(zlib.compress(raw, 6))
            cnt += 1
            if cnt >= limit:
                break
        if cnt >= limit:
            break
    if tot:
        print(f"  抽样 {cnt} 个: 原始 {tot/1048576:.2f} MB -> zlib 压缩 {comp/1048576:.2f} MB "
              f"（压缩率 {comp*100.0/tot:.1f}%，省 {(1-comp*1.0/tot)*100:.0f}%）")
        print(f"  按此推算全库 .sse {LEGACY_TOTAL_SSE_MB} MB -> "
              f"约 {LEGACY_TOTAL_SSE_MB*comp*1.0/tot:.0f} MB（git 内 zlib 存储）"
              f"   ⚠️ 该总量为 2026-09-16 实测值，引用前请现测")


DEFAULT_UNIQ_ROOTS = [("02-m1-evaluation", os.path.join(JVS, "02-m1-evaluation")),
                      ("03-d10-workspace", os.path.join(JVS, "03-d10-workspace"))]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=".sse ↔ response.json 一致性 / .sse 体积特性（合并自 verify_sse_equivalence{,2}.py）")
    ap.add_argument("cmd", choices=["compare", "sample", "uniq", "compress", "all"])
    ap.add_argument("--root", default=JVS, help="sample / compress 的遍历根；默认 JVS")
    ap.add_argument("--limit", type=int, default=None,
                    help="样本上限（compare 默认 6；sample 默认 400；compress 默认 30）")
    args = ap.parse_args(argv)

    if args.cmd in ("compare", "all"):
        cmd_compare(DEFAULT_BASES, args.limit or 6)
    if args.cmd in ("uniq", "all"):
        cmd_uniq(DEFAULT_UNIQ_ROOTS)
    if args.cmd in ("compress", "all"):
        cmd_compress(os.path.join(JVS, "02-m1-evaluation"), args.limit or 30)
    if args.cmd == "sample":
        cmd_sample(args.root, args.limit or 400)
    return 0


if __name__ == "__main__":
    sys.exit(main())
