# -*- coding: utf-8 -*-
"""对比 01 子树：备份(原始字节) vs 当前工作树，统计行尾变化范围。"""
import os

B = r"C:\Users\<user>\_jvs-tmp-01\2026-08-28-20-59-40"
D = r"C:\Users\<user>\Desktop\JVS\01-host-product\2026-08-28-20-59-40"

def eol(p):
    d = open(p, "rb").read()
    c = d.count(b"\r\n")
    l = d.count(b"\n") - c
    return c, l

same = diff = missing = 0
samples = []
for root, dirs, files in os.walk(B):
    dirs[:] = [x for x in dirs if x not in (".git", "__pycache__")]
    for f in files:
        bp = os.path.join(root, f)
        rel = os.path.relpath(bp, B)
        dp = os.path.join(D, rel)
        if not os.path.exists(dp):
            missing += 1
            continue
        cb, lb = eol(bp)
        cd, ld = eol(dp)
        if (cb, lb) == (cd, ld):
            same += 1
        else:
            diff += 1
            if len(samples) < 12:
                samples.append((rel, cb, lb, cd, ld))

print(f"备份 vs 当前工作树（仅 tracked 范围外也会走到，missing 表示被 gitignore 排除）")
print(f"  行尾一致 : {same}")
print(f"  行尾不同 : {diff}")
print(f"  当前缺失 : {missing}  (被 .gitignore 排除，正常)")
print()
print("行尾变化样例:")
for rel, cb, lb, cd, ld in samples:
    print(f"  备份 CRLF={cb:<5} LF={lb:<5} -> 当前 CRLF={cd:<5} LF={ld:<5}")
    print(f"     {rel[:100]}")