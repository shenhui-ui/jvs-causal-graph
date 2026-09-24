# -*- coding: utf-8 -*-
"""S5: 迁移后校验 —— 路径可达性 + 关键 SHA 比对 + 台账/校验器体检。

⚠️ **本脚本是「2026-09-16 迁移时刻」的一次性校验件，不是「当前树」校验器。**
`S1_KEYS` 的 9 个期望值**冻结在迁移时刻**，**不得**为了让它变绿而回填
（回填 = 改写迁移记录，与「版本目录只新增不改写」冲突）。

⇒ 迁移之后**任何被有意修改**的文件都会让它报 drift —— 这是**预期行为，不是回归**。
**已知 drift（现测 2026-09-21：相符 5 / 漂移 4）**：
  · `review_scoped_validator.py`      `648546f6…` → `b623c12f…`（2026-09-21 C-2 拆分首个目标所致）
  · `review_full_validator.py`        `d80372e0…` → `aa658ebe…`（2026-09-21 C-2 拆分第二个目标所致）
  · `build_acceptance_ledger.py`      `69a439ce…` → `321879c9…`（2026-09-20 阶段 B item 1 所致）
  · `outputs/m1-10-d10-holdout-20260916/questions.json` `0b526bc3…` → `ee5dd928…`
若将来确需「当前树」校验，请**另建**新脚本，勿改本件。
"""
import os, io, json, hashlib, ctypes, subprocess, time

BASE = r"C:\Users\<user>\host-workspace"
JVS = r"C:\Users\<user>\Desktop\JVS"
PM = os.path.join(BASE, "outputs", "migration-plan-20260916")

GFA = ctypes.windll.kernel32.GetFileAttributesW
GFA.restype = ctypes.c_uint32
GFA.argtypes = [ctypes.c_wchar_p]
INVALID = 0xFFFFFFFF

S1_KEYS = {
 r"2026-09-08-20-30-19\outputs\full-review-full-20260909\acceptance-ledger.json": "39f7bae9feb46d02",
 r"2026-09-08-20-30-19\review_scoped_validator.py": "648546f645c027a8",
 r"2026-09-08-20-30-19\review_full_validator.py": "d80372e0c7399e9e",
 r"2026-09-08-20-30-19\build_acceptance_ledger.py": "69a439ced2f23755",
 r"outputs\m1-10-d10-holdout-20260916\questions.json": "0b526bc31327b6a2",
 r"outputs\m1-10-d10-holdout-20260916\overview.md": "6f72e4da8e70ee6e",
 r"outputs\m1-10-d10-independence-check-20260916\report.md": "2eeafbcd6e98c794",
 r"2026-08-28-20-59-40\docs\M1-立项书-v0.6.md": "e510aa78aede6e0c",
 r"2026-08-28-20-59-40\docs\README-版本说明.md": "7a5a026e453ae643",
}

# 迁移后的物理位置（用于交叉验证 junction 指向正确）
PHYS = {
 "2026-08-28-20-59-40": r"01-host-product\2026-08-28-20-59-40",
 "2026-09-08-20-30-19": r"03-d10-workspace\2026-09-08-20-30-19",
 "restricted-review": r"04-restricted-materials\restricted-review",
}


def sha256(p, bufsize=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def exists(p):
    return GFA(p) != INVALID


def is_reparse(p):
    a = GFA(p)
    return a != INVALID and bool(a & 0x400)


def main():
    print("=" * 72)
    print("S5 迁移后校验")
    print("=" * 72)
    fails = []

    # --- 1. Junction 可达性 ---
    print("\n[1] 顶层 junction 状态")
    for rel in ["2026-08-28-20-59-40", "2026-09-08-20-30-19", "restricted-review"]:
        p = os.path.join(BASE, rel)
        ok = exists(p) and is_reparse(p) and os.path.isdir(p)
        print("   %s  reparse=%s dir=%s  %s" % ("PASS" if ok else "FAIL", is_reparse(p), os.path.isdir(p), rel))
        if not ok:
            fails.append("junction:" + rel)
    # outputs 目录本身 + 子项 junction
    out = os.path.join(BASE, "outputs")
    print("   outputs 本身: dir=%s reparse=%s (保留实体目录)" % (os.path.isdir(out), is_reparse(out)))
    subs = [d for d in sorted(os.listdir(out)) if os.path.isdir(os.path.join(out, d))]
    n_j = sum(1 for d in subs if is_reparse(os.path.join(out, d)))
    print("   outputs 子项: 共 %d 个目录, 其中 junction %d 个" % (len(subs), n_j))

    # --- 2. 关键 SHA 比对（走原路径，经 junction 解析）---
    print("\n[2] 关键文件 SHA（经原路径读取）")
    for rel, expect in S1_KEYS.items():
        p = os.path.join(BASE, rel)
        if not os.path.isfile(p):
            print("   MISSING  %s" % rel)
            fails.append("missing:" + rel)
            continue
        got = sha256(p)[:16]
        ok = got == expect
        print("   %s  %s  %s" % ("PASS" if ok else "FAIL", got, rel))
        if not ok:
            fails.append("sha:%s got=%s want=%s" % (rel, got, expect))

    # --- 3. 物理位置交叉验证：真实字节确在 JVS ---
    print("\n[3] 物理位置交叉验证（JVS 下直接读取，不经 junction）")
    for name, physrel in PHYS.items():
        pp = os.path.join(JVS, physrel)
        ok = os.path.isdir(pp)
        print("   %s  %s" % ("PASS" if ok else "FAIL", pp))
        if not ok:
            fails.append("phys:" + pp)
    # outputs 子项
    phys_out = os.path.join(JVS, "02-m1-evaluation", "outputs")
    n_phys = len([d for d in os.listdir(phys_out) if os.path.isdir(os.path.join(phys_out, d))]) if os.path.isdir(phys_out) else -1
    print("   %s  %s  (%d 个子目录)" % ("PASS" if n_phys >= 22 else "FAIL", phys_out, n_phys))
    if n_phys < 22:
        fails.append("phys-outputs")

    # --- 4. 文件总数复核 ---
    print("\n[4] 文件总数复核（迁移前 21,296）")
    total = 0
    for root in [os.path.join(JVS, "01-host-product"),
                 os.path.join(JVS, "02-m1-evaluation"),
                 os.path.join(JVS, "03-d10-workspace"),
                 os.path.join(JVS, "04-restricted-materials")]:
        if not os.path.isdir(root):
            continue
        n = 0
        for dp, dn, fn in os.walk(root):
            n += len(fn)
        total += n
    print("   JVS 物理文件数 = %d   差异 = %+d" % (total, total - 21296))
    if abs(total - 21296) > 6:   # 迁移期间 JVS\_migrate 会新增几个脚本
        fails.append("count:%d" % total)


    # --- 5. outputs 可读写 ---
    print("\n[5] outputs 可读写性（经原路径）")
    probe = os.path.join(BASE, "outputs", "_s5_probe.tmp")
    try:
        with io.open(probe, "w", encoding="utf-8") as f:
            f.write("probe")
        r = io.open(probe, encoding="utf-8").read()
        os.remove(probe)
        print("   PASS 写入/读取/删除均通过" if r == "probe" else "   FAIL 内容不符")
    except Exception as e:
        print("   FAIL %s" % e)
        fails.append("rw-probe")

    print("\n" + "=" * 72)
    print("结论: %s" % ("全部 PASS" if not fails else "存在 %d 项失败: %s" % (len(fails), fails)))
    print("=" * 72)
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())