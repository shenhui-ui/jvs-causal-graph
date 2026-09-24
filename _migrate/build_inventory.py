# -*- coding: utf-8 -*-
"""生成功能分类索引所需的完整清单。只读。"""
import os, io, json

JVS = r"C:\Users\<user>\Desktop\JVS"
OUT = r"C:\Users\<user>\Desktop\JVS\_migrate\inventory.json"


def listing(p, dirs=False, files=False):
    if not os.path.isdir(p):
        return []
    r = []
    for e in sorted(os.listdir(p)):
        fp = os.path.join(p, e)
        isd = os.path.isdir(fp)
        if isd and not dirs:
            continue
        if (not isd) and not files:
            continue
        r.append((e, isd))
    return r


def rc(p):
    n = 0
    for dp, dn, fn in os.walk(p):
        n += len(fn)
    return n


res = {}

# --- 01 宿主产品 ---
P01 = os.path.join(JVS, "01-host-product", "2026-08-28-20-59-40")
res["01_root_dirs"] = [e for e, d in listing(P01, dirs=True) if d]
res["01_root_files"] = [e for e, d in listing(P01, files=True) if not d]
res["01_docs"] = [e for e, d in listing(os.path.join(P01, "docs"), files=True) if not d]
res["01_tools"] = {m: len(listing(os.path.join(P01, "tools", m), files=True))
                   for m, d in listing(os.path.join(P01, "tools"), dirs=True) if d}
res["01_prompts"] = [e for e, d in listing(os.path.join(P01, "prompts"), files=True) if not d]

# --- host_memory_dump ---
HMD = os.path.join(P01, "docs", "host_memory_dump")
res["hmd_dirs"] = [e for e, d in listing(HMD, dirs=True) if d]
res["hmd_files"] = [e for e, d in listing(HMD, files=True) if not d]
res["hmd_counts"] = {"dirs": len(res["hmd_dirs"]), "files": len(res["hmd_files"]),
                     "total_files_recursive": rc(HMD)}
res["hmd_incoming"] = [e for e, d in listing(os.path.join(HMD, "incoming_real"), files=True) if not d]

# --- 02 评测 ---
P02O = os.path.join(JVS, "02-m1-evaluation", "outputs")
res["02_slices"] = [(e, rc(os.path.join(P02O, e))) for e, d in listing(P02O, dirs=True) if d]

# --- 03 D10 工作区 ---
P03 = os.path.join(JVS, "03-d10-workspace", "2026-09-08-20-30-19")
res["03_dirs"] = [e for e, d in listing(P03, dirs=True) if d]
f3 = [e for e, d in listing(P03, files=True) if not d]
res["03_file_count"] = len(f3)
res["03_ext"] = {}
for e in f3:
    ext = os.path.splitext(e)[1].lower() or "(none)"
    res["03_ext"][ext] = res["03_ext"].get(ext, 0) + 1
res["03_md"] = [e for e in f3 if e.lower().endswith(".md")]

# --- 04 受限 ---
P04 = os.path.join(JVS, "04-restricted-materials", "restricted-review")
res["04_dirs"] = {}
for e, d in listing(P04, dirs=True):
    if d:
        res["04_dirs"][e] = [x for x, y in listing(os.path.join(P04, e), files=True) if not y]

with io.open(OUT, "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)

for k in ["01_root_dirs", "01_root_files", "01_tools", "01_prompts",
          "hmd_counts", "02_slices", "03_ext", "03_file_count"]:
    print("---", k)
    print(json.dumps(res[k], ensure_ascii=False, indent=2)[:1500])
print("\n--- hmd_dirs (%d) ---" % len(res["hmd_dirs"]))
print(", ".join(res["hmd_dirs"]))
print("\n--- hmd_files (%d) ---" % len(res["hmd_files"]))
print(", ".join(res["hmd_files"][:60]))
print("\n--- hmd_incoming ---")
print(", ".join(res["hmd_incoming"]))
print("\n--- 03_dirs (%d) ---" % len(res["03_dirs"]))
print(", ".join(res["03_dirs"]))
print("\n--- 04_dirs ---")
print(json.dumps(res["04_dirs"], ensure_ascii=False, indent=2))
print("\n--- 01_docs (%d) ---" % len(res["01_docs"]))
print(", ".join(res["01_docs"]))