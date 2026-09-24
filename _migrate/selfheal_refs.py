# -*- coding: utf-8 -*-
"""refs 自愈 + 仓库加固。

背景：本环境两次出现 `.git/refs` 目录被清空，导致 git 判定「不是仓库」。
  （git 的 is_git_directory() 要求 refs 目录存在）
自愈依据：`.git/logs/refs/`（reflog）为权威现场，逐分支恢复最后一条有效目标。

加固（避免再次触发）：
  gc.auto = 0            —— 禁止自动 gc 擅自 pack/清理 ref
  maintenance.auto=false —— 禁止后台维护
  core.fsmonitor = false —— 禁用 fsmonitor（Windows 上易异常）
  core.logAllRefUpdates  —— 保留 reflog，作为自愈依据
"""
import os
import re
import subprocess
import sys

JVS = r"C:\Users\<user>\Desktop\JVS"
GIT = os.path.join(JVS, ".git")
REFLOG = os.path.join(GIT, "logs", "refs")

OK, WARN, FAIL = [], [], []


def sh(args, cwd=JVS):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def section(n, t):
    print(f"\n=== [{n}] {t} ===")


# ---------- [1] 强制建立 refs 目录树 ----------
section(1, "建立 refs 目录树")
for d in ["refs", "refs/heads", "refs/tags"]:
    p = os.path.join(GIT, *d.split("/"))
    os.makedirs(p, exist_ok=True)
    print(f"  [OK] .git/{d}/")
OK.append("refs 目录树就绪")

# ---------- [2] 从 reflog 恢复 loose ref ----------
section(2, "从 reflog 恢复 loose ref")
SHA = re.compile(r"\b([0-9a-f]{40})\b")
restored = {}
if os.path.isdir(REFLOG):
    for root, _dirs, files in os.walk(REFLOG):
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, os.path.join(GIT, "logs"))
            refname = rel.replace(os.sep, "/")
            try:
                lines = open(full, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            last = None
            for l in lines:
                m = SHA.findall(l)
                if len(m) >= 2:
                    last = m[1]
            if not last:
                continue
            p = os.path.join(GIT, *refname.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", newline="\n") as f:
                f.write(last + "\n")
            restored[refname] = last
            print(f"  {refname:36s} -> {last[:12]}")
else:
    FAIL.append("logs/refs 不存在，无法自愈")
    print("  [FAIL] logs/refs 缺失")

# ---------- [3] 确保 tags 存在 ----------
section(3, "确保里程碑标签")
TAGS = {
    "refs/tags/jvs-migration-20260916": "3b8c1ed75e9e228af6fd4d18506ed21d7f8a728c",
    "refs/tags/jvs-governance-20260916": "6541f05d03073561c0ac751b88090cc39a181c58",
    "refs/tags/d10-holdout-20260916": "1cb52bc859fe5fd1a1ec1082b2802326b9980eb1",
}
for ref, sha in TAGS.items():
    p = os.path.join(GIT, *ref.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", newline="\n") as f:
        f.write(sha + "\n")
    print(f"  {ref:36s} -> {sha[:12]}")

# ---------- [4] HEAD 指向 main ----------
section(4, "HEAD")
with open(os.path.join(GIT, "HEAD"), "w", newline="\n") as f:
    f.write("ref: refs/heads/main\n")
print("  HEAD -> refs/heads/main")

# ---------- [5] 加固配置 ----------
section(5, "加固配置")
CFG = [
    ("gc.auto", "0"),
    ("maintenance.auto", "false"),
    ("core.fsmonitor", "false"),
    ("core.logAllRefUpdates", "true"),
    ("core.autocrlf", "false"),
    ("core.eol", "lf"),
    ("core.quotePath", "false"),
    ("core.hooksPath", ".githooks"),
]
for k, v in CFG:
    code, out, err = sh(["git", "config", k, v])
    code2, o2, e2 = sh(["git", "config", "--get", k])
    print(f"  {k:26s} = {o2 or v}   {'[OK]' if (o2 or '') == v else '[WARN]'}")

# ---------- [6] 验证 ----------
section(6, "验证")
code, out, err = sh(["git", "rev-parse", "--git-dir"])
print(f"  git-dir : {out or err}")
if code != 0:
    FAIL.append("仓库仍不可识别")

code, out, err = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"])
print(f"  分支    : {out or err}")

code, out, err = sh(["git", "log", "--oneline", "-3"])
print(f"  log:")
for l in (out or err).splitlines():
    print(f"    {l}")

code, out, err = sh(["git", "tag", "-l"])
print(f"  标签    : {' / '.join((out or err).split())}")

code, out, err = sh(["git", "branch", "-a"])
print(f"  分支列表: {(out or err).replace(chr(10), ' ')}")

print("\n  refs 目录内容:")
for root, _d, files in os.walk(os.path.join(GIT, "refs")):
    for fn in files:
        rel = os.path.relpath(os.path.join(root, fn), os.path.join(GIT, "refs"))
        print(f"    {rel.replace(os.sep, '/')}")

# ---------- 汇总 ----------
print("\n" + "=" * 60)
print(f"OK={len(OK)}  WARN={len(WARN)}  FAIL={len(FAIL)}")
for w in WARN:
    print(f"  WARN - {w}")
for f in FAIL:
    print(f"  FAIL - {f}")
print("=" * 60)
sys.exit(1 if FAIL else 0)