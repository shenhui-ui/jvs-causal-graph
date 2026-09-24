# -*- coding: utf-8 -*-
"""S2-S4 v2: 续跑版。目标已移动的补建 junction；未移动的继续移动。
脚本必须放在 outputs 之外运行，否则锁住源目录。
用法: python s2s4_migrate2.py [--dry]
"""
import os, sys, io, json, time, ctypes, subprocess

BASE = r"C:\Users\<user>\host-workspace"
JVS = r"C:\Users\<user>\Desktop\JVS"
RUNDIR = os.path.join(JVS, "_migrate")

PLAN = [
    ("01-host-product",         "2026-08-28-20-59-40"),
    ("02-m1-evaluation",        "outputs"),
    ("03-d10-workspace",        "2026-09-08-20-30-19"),
    ("04-restricted-materials", "restricted-review"),
]

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
    """先试 rename；失败则试 MoveFileExW(MOVEFILE_COPY_ALLOWED)。"""
    try:
        os.rename(src, dst)
        return True, "rename"
    except OSError as e1:
        # MOVEFILE_REPLACE_EXISTING=1 | MOVEFILE_COPY_ALLOWED=2 | MOVEFILE_WRITE_THROUGH=8
        MF = ctypes.windll.kernel32.MoveFileExW
        MF.restype = ctypes.c_int
        MF.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        ok = MF(src, dst, 1 | 2 | 8)
        if ok:
            return True, "MoveFileEx"
        err = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
        return False, "rename:%s ; MoveFileEx failed w/errcode=0x%X" % (e1, ctypes.windll.kernel32.GetLastError())


def main():
    dry = "--dry" in sys.argv
    os.makedirs(RUNDIR, exist_ok=True)
    log("=== dry=%s | rundir=%s" % (dry, RUNDIR))

    log("--- S2 分区目录")
    for zone, _ in PLAN:
        zd = os.path.join(JVS, zone)
        if not dry:
            os.makedirs(zd, exist_ok=True)
    log("   ok")

    log("--- S3/S4 逐项处理")
    result = []
    for zone, name in PLAN:
        src = os.path.join(BASE, name)
        dst = os.path.join(JVS, zone, name)
        st = {"name": name, "src": src, "dst": dst,
              "src_exists": exists(src), "src_is_reparse": is_reparse(src) if exists(src) else None,
              "dst_exists": exists(dst)}
        log("== %s" % name)
        log("   src_exists=%s reparse=%s dst_exists=%s" % (st["src_exists"], st["src_is_reparse"], st["dst_exists"]))

        if st["dst_exists"]:
            # 已移动过 -> 只需保证 src 是 junction
            if dry:
                log("   [dry] ensure junction %s -> %s" % (src, dst))
            else:
                if st["src_exists"] and not st["src_is_reparse"]:
                    log("   !! 目标已存在但源仍在且非 junction —— 冲突，跳过")
                    st["action"] = "conflict"
                    result.append(st)
                    continue
                if exists(src) and is_reparse(src):
                    log("   junction 已存在，跳过")
                    st["action"] = "junction-exists"
                else:
                    rc = make_junction(src, dst)
                    st["action"] = "junction-created"
                    st["ok"] = (rc == 0 and is_reparse(src))
                    log("   junction rc=%s ok=%s" % (rc, st.get("ok")))
            result.append(st)
            continue

        if not st["src_exists"]:
            log("   !! 源与目标都不存在 —— 需人工确认")
            st["action"] = "missing"
            result.append(st)
            continue

        if dry:
            log("   [dry] move -> %s" % dst)
            result.append(st)
            continue

        t0 = time.time()
        ok, how = move_one(src, dst)
        log("   move ok=%s via %s (%.2fs)" % (ok, how, time.time() - t0))
        st["move_ok"] = ok
        st["move_how"] = how
        if not ok:
            st["action"] = "move-failed"
            result.append(st)
            continue
        rc = make_junction(src, dst)
        st["junction_rc"] = rc
        st["ok"] = (rc == 0 and is_reparse(src))
        st["action"] = "moved+junction"
        log("   junction rc=%s ok=%s" % (rc, st["ok"]))
        result.append(st)

    outdir = os.path.join(BASE, "outputs", "migration-plan-20260916")
    if exists(outdir) and not dry:
        with io.open(os.path.join(outdir, "s2s4-result.json"), "w", encoding="utf-8") as f:
            json.dump({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "items": result},
                      f, ensure_ascii=False, indent=2)
    log("=== done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())