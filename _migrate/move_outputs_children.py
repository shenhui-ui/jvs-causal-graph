# -*- coding: utf-8 -*-
"""02 分区专用：把 outputs 的每个子项搬到 JVS\\02-m1-evaluation\\outputs\\,
并在原位置留同名 Junction。outputs 目录本身保留（被宿主进程持有句柄）。
用法: python move_outputs_children.py [--dry]
"""
import os, sys, io, json, time, ctypes, subprocess

BASE = r"C:\Users\<user>\host-workspace"
SRC = os.path.join(BASE, "outputs")
DST = r"C:\Users\<user>\Desktop\JVS\02-m1-evaluation\outputs"

GFA = ctypes.windll.kernel32.GetFileAttributesW
GFA.restype = ctypes.c_uint32
GFA.argtypes = [ctypes.c_wchar_p]
INVALID = 0xFFFFFFFF


def is_reparse(p):
    a = GFA(p)
    return a != INVALID and bool(a & 0x400)


def exists(p):
    return GFA(p) != INVALID


def log(*a):
    print(*a, flush=True)


def make_junction(link, target):
    r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True)
    return r.returncode


def move_one(src, dst):
    try:
        os.rename(src, dst)
        return True, "rename"
    except OSError as e1:
        MF = ctypes.windll.kernel32.MoveFileExW
        MF.restype = ctypes.c_int
        MF.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        if MF(src, dst, 1 | 2 | 8):
            return True, "MoveFileEx"
        return False, "rename:%s ; MoveFileEx err=0x%X" % (e1, ctypes.windll.kernel32.GetLastError())


def main():
    dry = "--dry" in sys.argv
    if not dry:
        os.makedirs(DST, exist_ok=True)

    entries = sorted(os.listdir(SRC))
    log("=== dry=%s | %d entries" % (dry, len(entries)))

    items = []
    n_moved = n_junc = n_skip = n_fail = 0

    for e in entries:
        sp = os.path.join(SRC, e)
        dp = os.path.join(DST, e)
        st = {"name": e, "src": sp, "dst": dp}
        if os.path.isfile(sp):
            st["action"] = "skip-file"
            n_skip += 1
            log("SKIP  [file] %s" % e)
            items.append(st)
            continue
        if is_reparse(sp):
            st["action"] = "already-junction"
            n_skip += 1
            log("SKIP  [junction] %s" % e)
            items.append(st)
            continue
        if exists(dp):
            st["action"] = "dst-exists"
            n_skip += 1
            log("SKIP  [dst exists] %s" % e)
            items.append(st)
            continue
        if dry:
            log("[dry] move %s" % e)
            items.append(st)
            continue

        t0 = time.time()
        ok, how = move_one(sp, dp)
        if not ok:
            st["action"] = "move-failed"
            st["detail"] = how
            n_fail += 1
            log("FAIL  %-46s %s" % (e, how[:100]))
            items.append(st)
            continue
        rc = make_junction(sp, dp)
        good = (rc == 0 and is_reparse(sp))
        st["action"] = "moved+junction"
        st["ok"] = good
        st["sec"] = round(time.time() - t0, 3)
        n_moved += 1
        if good:
            n_junc += 1
        else:
            n_fail += 1
        log("OK    %-46s %.2fs junc=%s" % (e, time.time() - t0, good))
        items.append(st)

    log("=== moved=%d junction=%d skip=%d fail=%d" % (n_moved, n_junc, n_skip, n_fail))

    if not dry:
        outdir = os.path.join(BASE, "outputs", "migration-plan-20260916")
        if exists(outdir):
            with io.open(os.path.join(outdir, "s2_children-result.json"), "w", encoding="utf-8") as f:
                json.dump({"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "src": SRC, "dst": DST,
                           "counts": {"moved": n_moved, "junc": n_junc, "skip": n_skip, "fail": n_fail},
                           "items": items}, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())