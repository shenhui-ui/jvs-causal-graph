# -*- coding: utf-8 -*-
"""在为 JVS 定行尾策略前，量化 * -text 对 01 子树的字节影响。
同时读取 01 的 .gitignore / .gitattributes 原文。
"""
import os, subprocess

P01 = r"C:\Users\<user>\Desktop\JVS\01-host-product\2026-08-28-20-59-40"

def run(args, **kw):
    r = subprocess.run(args, cwd=P01, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300, **kw)
    return (r.stdout or "") + (r.stderr or "")

print("=" * 72)
print("01/.gitignore 原文")
print("=" * 72)
p = os.path.join(P01, ".gitignore")
print(open(p, encoding="utf-8", errors="replace").read() if os.path.exists(p) else "(不存在)")

print("=" * 72)
print("01/.gitattributes 原文")
print("=" * 72)
p = os.path.join(P01, ".gitattributes")
print(open(p, encoding="utf-8", errors="replace").read() if os.path.exists(p) else "(不存在)")

print("=" * 72)
print("若改用 * -text（字节冻结），01 子树会浮出多少差异文件？")
print("=" * 72)
lsf = run(["git", "-c", "core.quotepath=false", "ls-files", "-s"])
entries = []
for line in lsf.splitlines():
    if "\t" not in line:
        continue
    meta, path = line.split("\t", 1)
    parts = meta.split()
    if len(parts) < 3:
        continue
    entries.append((parts[1], path))      # (blob sha1 in index, path)
print(f"  tracked 文件数: {len(entries)}")

paths = [p for _, p in entries]
raw = run(["git", "hash-object", "-t", "blob", "--no-filters", "--stdin-paths"],
          input="\n".join(paths) + "\n")
raw_hashes = raw.splitlines()
print(f"  原始字节 hash 计算完成: {len(raw_hashes)} 个")

diff = []
for (idx_sha, path), raw_sha in zip(entries, raw_hashes):
    if idx_sha != raw_sha:
        diff.append((path, idx_sha[:10], raw_sha[:10]))

print(f"  差异文件数: {len(diff)}  ({(len(diff)*100.0/max(len(entries),1)):.1f}%)")
print()
if diff:
    print("  差异清单（这些文件的工作树字节与库内 blob 不同，原因几乎都是行尾）:")
    for path, a, b in diff[:80]:
        print(f"    {path}")
else:
    print("  无差异 —— 说明 * -text 不会让任何文件浮出为 modified。")

print()
print("=" * 72)
print("这些差异文件的行尾实况（抽样）")
print("=" * 72)
for path, a, b in diff[:12]:
    fp = os.path.join(P01, path.replace("/", os.sep))
    try:
        data = open(fp, "rb").read()
        crlf = data.count(b"\r\n")
        lf = data.count(b"\n") - crlf
        print(f"    wt: CRLF={crlf:<6} LF={lf:<6}  {path}")
    except Exception as e:
        print(f"    读取失败 {path}: {e}")

print()
print("=" * 72)
print("结论")
print("=" * 72)
print(f"  01 子树当前 clean 状态（git status 只报 1 个 modified）是 text=auto 归一化的结果。")
print(f"  改用 * -text 后，上述 {len(diff)} 个文件会因行尾差异浮出。")
print(f"  处理方式：把它们的工作树字节固化为 JVS 基线（字节不变，符合冻结原则）。")