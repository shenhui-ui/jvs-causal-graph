#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""旧根引用守卫 —— git-gate [11] 判据源（2026-10-20 立项 A-lite，项目方授权）。

目标：防**新增**旧根引用进入主线，不追究历史存量（3,645 件已登记为
「已知不可运行 / 已知留痕」，见 _handoff/OLD-ROOT-EXEMPTION.md）。
⚠️ 反例（勿回退）：全库 `grep -r` 判据会让 324+ 处合法留痕立刻假红。

判据：
  · 扫 `git diff <base>...HEAD -U0` 的**新增/修改行**（前缀 '+'，跳过 '+++' 头）；
  · 命中旧根**路径形状**（旧根目录名 + 尾随 '\' 或 '/'）-> 违规；
  · 违规行所属文件在白名单（scripts/old-root-exemptions.txt）-> 放行；
  · 白名单缺失或为空 -> FAIL（反假绿：判据源缺失不得放行，同 git-gate [5] 步纪律）。

自证（--self-test 四测，夹具为内存合成 diff，不触碰工作区；沿用
test_llm_judge_rationale --self-test 先例 —— 判据必须「既能拒绝、也不是恒拒」）：
  A 新增行含旧根路径形状        -> 必须 FAIL
  B 仅上下文行含旧根（未改动）  -> 必须 PASS   —— 证明不追究存量
  C 白名单文件的新增行含旧根    -> 必须 PASS   —— 证明白名单生效
  D 白名单缺失 / 为空           -> 必须 FAIL   —— 反假绿

输出纪律：不打印 [OK]/[FAIL]/[WARN]（git-gate bash 侧专属 token），用 [违规]。
用法：
  python scripts/old-root-guard.py            # 实扫（默认 base=main）
  python scripts/old-root-guard.py --quiet    # 同上，通过时零输出
  python scripts/old-root-guard.py --self-test
"""
import argparse
import io
import os
import re
import subprocess
import sys
import tempfile

# ⚠️ 自指规避：判据串拼装构造，本文件源码不得出现连续的旧根目录名字面量
#   （否则守卫自身进入命中集；同 _migrate/make_progress_md.py L138 铁理）。
NEEDLE = "WorkBuddy" + " " + "AI"
SHAPE = re.compile(re.escape(NEEDLE) + r"[\\/]")
EXEMPTIONS = os.path.join("scripts", "old-root-exemptions.txt")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QUIET_FLAG = [False]


def log(msg):
    if not QUIET_FLAG[0]:
        print(msg)


def load_exemptions(path):
    """返回 (ok, 集合)：文件缺失或无有效条目 -> (False, …)。"""
    if not os.path.isfile(path):
        return False, set()
    with open(path, encoding="utf-8") as fh:
        entries = {ln.strip() for ln in fh
                   if ln.strip() and not ln.lstrip().startswith("#")}
    return bool(entries), entries


def scan(diff_text, exemptions):
    """解析 -U0 diff，返回 (违规列表, 新增行数, 涉及文件数)。"""
    violations, n_add, files = [], 0, set()
    cur = None
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
        elif line.startswith("+"):
            if line.startswith("+++"):
                continue
            n_add += 1
            files.add(cur)
            if cur is not None and cur not in exemptions and SHAPE.search(line):
                violations.append((cur, line[1:120]))
    return violations, n_add, len(files)


def run_scan(base, exemptions_path, quiet):
    QUIET_FLAG[0] = quiet
    ok, exemptions = load_exemptions(exemptions_path)
    if not ok:
        # ⚠️ 违规/判据源问题必须**无条件下报**——quiet 只静默成功路径的汇总行
        QUIET_FLAG[0] = False
        log(f"[违规] 白名单缺失或为空: {exemptions_path} —— 判据源缺失，不得以此放行")
        return 1
    try:
        diff = subprocess.run(
            ["git", "diff", f"{base}...HEAD", "-U0"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except OSError as e:
        QUIET_FLAG[0] = False
        log(f"[违规] 无法执行 git diff: {e}")
        return 1
    if diff.returncode != 0:
        QUIET_FLAG[0] = False
        log(f"[违规] git diff {base}...HEAD 失败: {diff.stderr.strip().splitlines()[:1]}")
        return 1
    violations, n_add, n_files = scan(diff.stdout, exemptions)
    if violations:
        QUIET_FLAG[0] = False
        for path, snippet in violations[:20]:
            log(f"[违规] {path}: {snippet}")
        if len(violations) > 20:
            log(f"[违规] ……（另有 {len(violations) - 20} 处）")
        return 1
    log(f"新增/修改行 {n_add} 行（{n_files} 文件），旧根路径命中 0；白名单在位")
    return 0


def self_test():
    """四测：A 拒 / B 不追究存量 / C 白名单 / D 反假绿。全过打印自证串。"""
    old_root_line = "P = r'C:" + "\\" + "Users" + "\\" + "<user>" + "\\" \
        + NEEDLE + "\\" + "2026-09-08-20-30-19'"
    diff_a = f"--- a/demo.py\n+++ b/demo.py\n@@ -1 +1 @@\n-old\n+{old_root_line}\n"
    diff_b = f"--- a/keep.py\n+++ b/keep.py\n@@ -1,1 +1,1 @@\n {old_root_line}\n"
    diff_c = f"--- a/_handoff/OLD-ROOT-EXEMPTION.md\n+++ b/_handoff/OLD-ROOT-EXEMPTION.md\n@@ -1 +1 @@\n+{old_root_line}\n"
    cases = []

    v, _, _ = scan(diff_a, set())
    cases.append(("A 新增行含旧根路径形状 -> FAIL", len(v) == 1 and v[0][0] == "demo.py"))

    v, n, _ = scan(diff_b, set())
    cases.append(("B 未改动行含旧根 -> PASS", len(v) == 0 and n == 0))

    v, _, _ = scan(diff_c, {"_handoff/OLD-ROOT-EXEMPTION.md"})
    cases.append(("C 白名单登记后 -> PASS", len(v) == 0))

    ok_d, _ = load_exemptions(os.path.join(tempfile.gettempdir(), "no-such-file.txt"))
    cases.append(("D 白名单缺失 -> FAIL", ok_d is False))
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as tf:
        tf.write("# only comments\n\n")
        empty_list = tf.name
    try:
        ok_e, _ = load_exemptions(empty_list)
        cases.append(("D' 白名单为空 -> FAIL", ok_e is False))
    finally:
        os.unlink(empty_list)

    passed = all(ok for _, ok in cases)
    for name, ok in cases:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print("旧根守卫自证：既能拒绝、也不是恒拒（四测" + ("全过）" if passed else "存在失败）"))
    return 0 if passed else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="main")
    ap.add_argument("--exemptions", default=EXEMPTIONS)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    return run_scan(args.base, args.exemptions, args.quiet)


if __name__ == "__main__":
    sys.exit(main())
