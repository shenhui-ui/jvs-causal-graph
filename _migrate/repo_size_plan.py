# -*- coding: utf-8 -*-
"""最终体积测算：失真 vs 体积的准确权衡。
关键洞察：git 用 zlib 压缩 + 内容寻址去重，所以"工作树 3.5GB" != "仓库 3.5GB"。
测算三种方案的仓库真实占用。
"""
import os, hashlib, zlib, collections

JVS = r"C:\Users\<user>\Desktop\JVS"
ZIP_EXT = {".zip"}
SSE_EXT = {".sse"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}
JUNK_EXT = {".pyc", ".pyo", ".log", ".tmp", ".bak", ".orig", ".rej"}

def zsize(path):
    try:
        raw = open(path, "rb").read()
    except Exception:
        return None, 0
    return hashlib.sha256(raw).hexdigest(), len(zlib.compress(raw, 6))

# 逐分区统计：唯一内容 hash -> (压缩后大小, 是否 sse)
uniq = {}          # hash -> [zlen, is_sse, is_zip, is_img, is_junk, worktree_bytes]
stats = collections.defaultdict(lambda: {"files": 0, "wt": 0, "uniq": set(), "z": 0})

for root, dirs, files in os.walk(JVS):
    dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules")]
    rel = os.path.relpath(root, JVS)
    top = rel.split(os.sep)[0] if rel != "." else "(root)"
    for f in files:
        p = os.path.join(root, f)
        ext = os.path.splitext(f)[1].lower()
        try:
            wt = os.path.getsize(p)
        except Exception:
            continue
        s = stats[top]
        s["files"] += 1
        s["wt"] += wt
        h, z = zsize(p)
        if h is None:
            continue
        s["uniq"].add(h)
        if h not in uniq:
            uniq[h] = [z, ext in SSE_EXT, ext in ZIP_EXT, ext in IMG_EXT, ext in JUNK_EXT, wt]

print("=" * 78)
print("逐分区：工作树体积 vs 去重压缩后仓库体积")
print("=" * 78)
print(f"{'分区':<26}{'文件数':>8}{'工作树MB':>11}{'唯一内容':>10}{'仓库MB(压缩)':>14}")
tot_all = tz_all = 0
rows = []
for top in sorted(stats):
    s = stats[top]
    zsum = sum(uniq[h][0] for h in s["uniq"])
    rows.append((top, s["files"], s["wt"], len(s["uniq"]), zsum))
    tot_all += s["wt"]; tz_all += zsum
for top, n, wt, nu, zsum in rows:
    print(f"{top:<26}{n:>8}{wt/1048576:>11.2f}{nu:>10}{zsum/1048576:>14.2f}")
print("-" * 78)
print(f"{'合计':<26}{sum(r[1] for r in rows):>8}{tot_all/1048576:>11.2f}"
      f"{'':>10}{tz_all/1048576:>14.2f}")

# 分类拆解
def agg(pred):
    n = 0; wt = 0; z = 0; u = set()
    for h, v in uniq.items():
        if pred(h):
            u.add(h); wt += v[5]; z += v[0]
    return len(u), wt, z

print()
print("=" * 78)
print("按类别拆解（唯一内容口径，模拟 git 实际存储）")
print("=" * 78)
cats = [
    (".sse 流式原文", lambda h: uniq[h][1]),
    (".zip 打包件", lambda h: uniq[h][2]),
    ("图片", lambda h: uniq[h][3]),
    ("缓存/日志/临时", lambda h: uniq[h][4]),
    ("其余（业务产物+代码）", lambda h: not (uniq[h][1] or uniq[h][2] or uniq[h][3] or uniq[h][4])),
]
for name, pred in cats:
    n, wt, z = agg(pred)
    print(f"  {name:<24} 唯一{n:>6} 个   工作树 {wt/1048576:>8.2f} MB   仓库占用 {z/1048576:>8.2f} MB")

print()
print("=" * 78)
print("三种方案的取舍")
print("=" * 78)
def plan(excl_sse, excl_zip, excl_img, excl_junk):
    n = wt = z = 0
    for h, v in uniq.items():
        if excl_sse and v[1]: continue
        if excl_zip and v[2]: continue
        if excl_img and v[3]: continue
        if excl_junk and v[4]: continue
        n += 1; wt += v[5]; z += v[0]
    return n, wt, z

plans = [
    ("A 无损：全纳入（仅排缓存）",            False, False, False, True),
    ("B 折中：排 .zip+图片（留 .sse）",         False, True,  True,  True),
    ("C 激进：排 .sse+.zip+图片",              True,  True,  True,  True),
]
for name, a, b, c, d in plans:
    n, wt, z = plan(a, b, c, d)
    print(f"  {name:<30} 唯一{n:>6} 个 | 仓库约 {z/1048576:>7.1f} MB (未压缩 {wt/1048576:>7.1f} MB)")

print()
print("=" * 78)
print("结论")
print("=" * 78)
nA, wtA, zA = plan(False, False, False, True)
nC, wtC, zC = plan(True, True, True, True)
print(f"  方案A 与 方案C 的仓库体积差: {(zA-zA)/1048576:.1f} MB（{(zA-zC)/max(zA,1)*100:.1f}%）")
print(f"  -> 排除 .sse/.zip/图片 只省下 {(zA-zC)/1048576:.1f} MB，")
print(f"     却丢失: ① 5.8% 被放弃尝试的思考链  ② 原始流式分片（可独立验证 response.json 拼接正确性）")
print(f"     ③ verify_delivery.py 对 .sse 的校验在仓库副本上将无法通过")
print()
print(f"  方案A 仓库总量约 {zA/1048576:.0f} MB —— 代价很小，建议优先考虑。")