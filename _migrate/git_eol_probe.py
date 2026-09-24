# -*- coding: utf-8 -*-
"""行尾与 git 属性探测：决定 .gitattributes / core.autocrlf 怎么写才不会改字节。"""
import os, subprocess, collections

JVS = r"C:\Users\<user>\Desktop\JVS"
P01 = os.path.join(JVS, "01-host-product", "2026-08-28-20-59-40")

def run(c, cwd):
    r = subprocess.run(c, cwd=cwd, shell=True, capture_output=True, text=True,
                       timeout=120, errors="replace")
    return ((r.stdout or "") + (r.stderr or "")).strip()

print("=" * 70)
print("1. 全库行尾分布（按分区×扩展名）")
print("=" * 70)
stat = collections.defaultdict(lambda: [0, 0, 0])   # (top, ext) -> [LF, CRLF, MIXED/BIN]
for root, dirs, files in os.walk(JVS):
    dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules")]
    rel = os.path.relpath(root, JVS)
    top = rel.split(os.sep)[0]
    for f in files:
        ext = os.path.splitext(f)[1].lower() or "(noext)"
        p = os.path.join(root, f)
        try:
            if os.path.getsize(p) > 2 * 1024 * 1024:
                continue
            b = open(p, "rb").read()
        except Exception:
            continue
        if b"\x00" in b[:4096]:
            stat[(top, ext)][2] += 1
            continue
        crlf = b.count(b"\r\n")
        lf = b.count(b"\n") - crlf
        k = stat[(top, ext)]
        if crlf and lf:
            k[2] += 1
        elif crlf:
            k[1] += 1
        else:
            k[0] += 1
for (top, ext) in sorted(stat):
    lf, crlf, other = stat[(top, ext)]
    if lf + crlf + other < 5:
        continue
    print(f"  {top:<24} {ext:<10} LF={lf:<6} CRLF={crlf:<6} 混合/二进制={other}")

print()
print("=" * 70)
print("2. 01 仓的 git 属性与行尾（这是既有历史，必须原样保住）")
print("=" * 70)
print("  根 .gitattributes 内容:")
ga = os.path.join(P01, ".gitattributes")
if os.path.exists(ga):
    for line in open(ga, encoding="utf-8", errors="ignore").read().splitlines():
        print("     " + line)
else:
    print("     (无)")
print("  仓级 core.autocrlf :", run("git config --local core.autocrlf", P01) or "(未设置->继承全局 true)")
print("  仓级 core.eol      :", run("git config --local core.eol", P01) or "(未设置)")

# 取 01 中若干 tracked 文件，比对 工作树字节 vs 库里 blob 字节
print()
print("  工作树 vs 库内 blob 行尾对比（抽样 12 个 tracked 文件）:")
tracked = run("git ls-files", P01).splitlines()
sample = [f for f in tracked if f.endswith((".py", ".md", ".json", ".gitignore", ".gitattributes"))][:12]
for f in sample:
    p = os.path.join(P01, f.replace("/", os.sep))
    try:
        wt = open(p, "rb").read()
    except Exception:
        continue
    blob = subprocess.run(["git", "cat-file", "-p", "HEAD:" + f], cwd=P01,
                          capture_output=True, timeout=60).stdout
    def lfcrlf(b):
        crlf = b.count(b"\r\n")
        lf = b.count(b"\n") - crlf
        return ("CRLF" if crlf and not lf else "LF" if lf and not crlf else
                "MIXED" if crlf and lf else "none")
    same = "同" if wt == blob else "!! 不同 !!"
    print(f"    {same:<12} wt={lfcrlf(wt):<7} blob={lfcrlf(blob):<7} {f}")

print()
print("=" * 70)
print("3. 关键产物 SHA（git 化后必须不变）")
print("=" * 70)
import hashlib
KEYS = [
    r"01-host-product\2026-08-28-20-59-40\tools\causal\s4_pipeline.py",
    r"01-host-product\2026-08-28-20-59-40\tools\causal\llm_judge.py",
    r"03-d10-workspace\2026-09-08-20-30-19\outputs\full-review-full-20260909\acceptance-ledger.json",
    r"03-d10-workspace\2026-09-08-20-30-19\outputs\full-review-full-20260909\final-review-package\event-reviews.json",
    r"02-m1-evaluation\outputs\m1-10-d10-holdout-20260916\holdout-questions.json",
]
res = {}
for k in KEYS:
    p = os.path.join(JVS, k)
    if not os.path.exists(p):
        print(f"  (缺) {k}")
        continue
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    res[k] = h
    print(f"  {h[:24]}  {h[:0]}{k}")
    print(f"       size={os.path.getsize(p)}")
print()
print("我会把上面这些 SHA 写进基线指纹，git 化完成后逐一复核。")