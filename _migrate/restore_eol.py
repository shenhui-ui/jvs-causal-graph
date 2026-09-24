# -*- coding: utf-8 -*-
"""根治方案：把 01 子树 408 个文件的原始字节从备份恢复，
并在 01 子树内放一个 * -text 的 .gitattributes 覆盖其 text=auto。
恢复后 git 会把这些文件按原始字节重新入库。
"""
import os, shutil, hashlib

B = r"C:\Users\<user>\_jvs-tmp-01\2026-08-28-20-59-40"
D = r"C:\Users\<user>\Desktop\JVS\01-host-product\2026-08-28-20-59-40"

def eol(p):
    d = open(p, "rb").read()
    c = d.count(b"\r\n")
    return c, d.count(b"\n") - c

restored = []
for root, dirs, files in os.walk(B):
    dirs[:] = [x for x in dirs if x not in (".git", "__pycache__")]
    for f in files:
        bp = os.path.join(root, f)
        rel = os.path.relpath(bp, B)
        dp = os.path.join(D, rel)
        if not os.path.exists(dp):
            continue
        if eol(bp) != eol(dp):
            shutil.copy2(bp, dp)
            restored.append(rel)

print(f"已从备份恢复原始字节: {len(restored)} 个文件")

# 放覆盖用 .gitattributes 到 01 子树（覆盖其自带 text=auto）
# 位置：2026-08-28-20-59-40/.gitattributes 已存在且含 text=auto，
# 需要改写它 —— 但它是 tracked 文件，改写会形成内容变更。
# 更稳妥：保留原文件，在其末尾追加覆盖规则。
ga = os.path.join(D, ".gitattributes")
orig = open(ga, encoding="utf-8").read()
marker = "# --- JVS: byte-freeze override (2026-09-16) ---"
if marker not in orig:
    with open(ga, "a", encoding="utf-8", newline="") as fh:
        fh.write("\n" + marker + "\n")
        fh.write("# 本仓库采用字节冻结策略：以下规则覆盖上方 * text=auto，\n")
        fh.write("# 避免签出时把 CRLF 归一化为 LF 而破坏需 SHA256 核验的产物。\n")
        fh.write("* -text\n")
        for ext in ("*.py", "*.md", "*.txt", "*.json", "*.jsonl",
                    "*.yaml", "*.yml", "*.toml", "*.csv"):
            fh.write(f"{ext} -text\n")
        fh.write("*.db binary\n*.sqlite binary\n*.sqlite3 binary\n")
    print(f"已在 {ga} 追加字节冻结覆盖规则")
else:
    print(f"{ga} 已含覆盖规则，跳过")

# 校验恢复结果
still = 0
for root, dirs, files in os.walk(B):
    dirs[:] = [x for x in dirs if x not in (".git", "__pycache__")]
    for f in files:
        bp = os.path.join(root, f)
        dp = os.path.join(D, os.path.relpath(bp, B))
        if os.path.exists(dp) and eol(bp) != eol(dp):
            still += 1
print(f"仍未恢复: {still} 个")