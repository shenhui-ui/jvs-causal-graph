#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jvs-arbitrate.py —— JVS 多会话并发提交的「优先级列表式」仲裁合并器

设计原则
--------
1. **优先级来自仓库外真值源**（`_handoff/agents/PRIORITY.json`），
   提交信息里写的一切「优先级」一律忽略（trailer 只承载**身份**，不承载权力）。
2. **不按提交时间仲裁**，按 PRIORITY.json 声明的数字（大者胜）。
3. **全程 plumbing**：不碰工作区、不碰 `.git/index`、不碰 `.git/HEAD`（可安全并发运行）。
4. **生成物豁免**：`_index/` 与 PENDING.md / SCRIPTS.md 的冲突不仲裁，
   一律保留 ours 并列入「待重生成」（它们应由生成器产出）。
5. **必须能拒绝**：低优先级方触碰了高优先级方独有的路径 ⇒ 拒绝并转人工，
   而不是静默 `-X ours` 通过。

用法
----
    python jvs-arbitrate.py --dry-run            # 只探测，不改任何 ref（默认）
    python jvs-arbitrate.py --apply              # 真的推进 main（CAS）
    python jvs-arbitrate.py --apply --only <ref> # 只听从一个候选（调试用）
    python jvs-arbitrate.py --selfcheck          # 「能拒绝」负向自证（在临时夹具里跑）

退出码：0 = 全部合并成功；3 = 有候选被拒绝 / 需人工；1 = 判据故障。
"""

import argparse
import json
import os
import re
import subprocess
import sys

JVS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIORITY_FILE = os.path.join(JVS, "_handoff", "agents", "PRIORITY.json")

SESSION_TRAILER = "JVS-Session"


# ---------------------------------------------------------------- git helpers
def git(*args, repo=None, check=True, input_bytes=None):
    """跑一条 git，返回 (rc, stdout_str, stderr_str)。绝不抛异常除非 check。"""
    p = subprocess.run(
        ["git", "-C", repo or JVS] + list(args),
        capture_output=True, input=input_bytes,
    )
    out = p.stdout.decode("utf-8", "replace")
    err = p.stderr.decode("utf-8", "replace")
    if check and p.returncode != 0:
        raise RuntimeError("git %s 失败 rc=%d\n%s" % (" ".join(args), p.returncode, err))
    return p.returncode, out, err


def rev(repo, spec):
    rc, out, _ = git("rev-parse", "--verify", "--quiet", spec, repo=repo, check=False)
    return out.strip() if rc == 0 and out.strip() else None


def commit_session(repo, sha):
    """从提交信息 trailer 读会话身份。读不到 ⇒ None。"""
    rc, out, _ = git("log", "-1", "--format=%B", sha, repo=repo, check=False)
    if rc != 0:
        return None
    rc2, parsed, _ = git("interpret-trailers", "--parse", "--no-divider",
                         repo=repo, check=False, input_bytes=out.encode("utf-8"))
    if rc2 != 0:
        return None
    for line in parsed.splitlines():
        if line.startswith(SESSION_TRAILER + ":"):
            return line.split(":", 1)[1].strip()
    return None


def commit_subject(repo, sha):
    rc, out, _ = git("log", "-1", "--format=%s", sha, repo=repo, check=False)
    return out.strip() if rc == 0 else "?"


# ------------------------------------------------------------- priority truth
def load_priority(path=PRIORITY_FILE):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg


def priority_of(cfg, session):
    """会话身份 → 优先级数字。未声明会话 ⇒ unmarked_priority（对待 main 上无署名内容）。"""
    if session is None:
        return cfg["unmarked_priority"], "ad-hoc(未署名)"
    s = cfg["sessions"].get(session)
    if s is None:
        return cfg["ad-hoc"] if False else cfg["default_priority"], "未登记会话:%s" % session
    return s["priority"], s["desc"]


def is_generated(cfg, path):
    p = path.replace("\\", "/").lstrip("./")
    for pre in cfg.get("generated_prefixes", []):
        if p.startswith(pre):
            return True
    if p in [x.replace("\\", "/") for x in cfg.get("generated_files", [])]:
        return True
    return False


# --------------------------------------------------------------- merge engine
CONFLICT_HUNK = re.compile(rb"^<{7}[ \t]|^={7}[ \t]*$|^>{7}[ \t]", re.M)


def merge_tree(repo, base, ours, theirs, strategy=None):
    """调 merge-tree，返回 dict(tree, conflicts, rc)。不碰工作区/index/HEAD。

    ⭐ 关键：合并树必须来自 merge-tree 的三方结果，
    **不能**用 `theirs^{tree}` —— 那会静默回退 ours 上的并发改动（本轮实测踩到）。
    """
    args = ["merge-tree", "--write-tree", "--name-only", "-z"]
    if strategy:
        args += ["-X", strategy]
    args += [ours, theirs]
    rc, out, err = git(*args, repo=repo, check=False)

    parts = out.split("\x00")
    if not parts or not parts[0].strip():
        raise RuntimeError("merge-tree 未返回树：%s" % err)
    tree = parts[0].strip()

    # -z 模式下，冲突路径以 "NUL <stage-count> NUL <path> NUL" 形式出现
    conflicts = []
    i = 1
    while i < len(parts):
        tok = parts[i]
        if tok.strip().isdigit():
            # 下一项是路径
            if i + 1 < len(parts) and parts[i + 1].strip():
                conflicts.append(parts[i + 1].strip())
            i += 2
        else:
            i += 1
    # 去重保序
    seen, uniq = set(), []
    for c in conflicts:
        if c not in seen:
            seen.add(c); uniq.append(c)
    return {"tree": tree, "conflicts": uniq, "rc": rc}


def changed_paths(repo, base, commit):
    rc, out, _ = git("diff", "--name-only", "-z", base, commit, repo=repo, check=False)
    if rc != 0:
        return set()
    return {p for p in out.split("\x00") if p}


def changed_set(repo, base, commit):
    """返回 {path: (old_sha_or_None, new_sha_or_None)}。"""
    rc, out, _ = git("diff", "--raw", "-z", base, commit, repo=repo, check=False)
    res = {}
    if rc != 0:
        return res
    toks = [t for t in out.split("\x00") if t]
    idx = 0
    while idx < len(toks):
        meta = toks[idx]
        if meta.startswith(":"):
            parts = meta[1:].split()
            if len(parts) >= 5:
                old, new, status = parts[2], parts[3], parts[4]
                if idx + 1 < len(toks):
                    path = toks[idx + 1]
                    res[path] = (old, new, status)
                    idx += 2
                    continue
        idx += 1
    return res


def apply_ours_on_conflict(repo, tree_sha, ours_tree, files):
    """把 tree_sha 里若干路径替换为 ours_tree 的同名条目。返回新树 sha。

    用**临时索引**（GIT_INDEX_FILE）做，绝不碰仓库共享的 `.git/index`。
    ⚠️ 不能用 `git mktree`：它不接受含 `/` 的路径（实测
       `fatal: path _index/a.json contains slash`）。
    """
    if not files:
        return tree_sha
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix="jvs-arb-idx-")
    os.close(fd)
    os.remove(tmp)
    env_idx = tmp
    try:
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = env_idx

        def g2(*args, **kw):
            p = subprocess.run(["git", "-C", repo] + list(args),
                               capture_output=True, env=env, **kw)
            return p.returncode, p.stdout.decode("utf-8", "replace"), \
                   p.stderr.decode("utf-8", "replace")

        rc, _, err = g2("read-tree", tree_sha)
        if rc != 0:
            raise RuntimeError("read-tree 失败：%s" % err)

        target = set(files)
        for path in sorted(target):
            rc, out, _ = g2("ls-tree", "-z", ours_tree, "--", path)
            got = None
            for it in out.split("\x00"):
                if it:
                    m2, _, p2 = it.partition("\t")
                    ff = m2.split()
                    if len(ff) >= 3 and p2 == path:
                        got = ff
            if got:
                rc, _, err = g2("update-index", "--add", "--cacheinfo",
                                "%s,%s,%s" % (got[0], got[2], path))
                if rc != 0:
                    raise RuntimeError("update-index %s 失败：%s" % (path, err))
            else:
                # ours 侧不存在该路径 ⇒ 从结果中删除
                rc, _, err = g2("update-index", "--force-remove", "--", path)
                if rc != 0:
                    raise RuntimeError("update-index --force-remove %s 失败：%s" % (path, err))
        rc, out, err = g2("write-tree")
        if rc != 0:
            raise RuntimeError("write-tree 失败：%s" % err)
        return out.strip()
    finally:
        for suffix in ("", ".lock"):
            try:
                os.remove(env_idx + suffix)
            except OSError:
                pass


# ------------------------------------------------------------------- the core
def arbitrate(repo, main_sha, candidates, cfg, log=print):
    """按优先级降序把 candidates 逐个并入 main_sha。

    candidates: [(sha, session, priority, desc)]，**乱序传入**，本函数内部排序。
    返回 (new_main_sha|None, merged[], rejected[], regen[])。
    """
    # ⭐ 排序：优先级降序；同优先级时按提交时间升序（先到先得，稳定）
    ordered = sorted(
        candidates,
        key=lambda c: (-c[2], int(git("log", "-1", "--format=%ct", c[0], repo=repo,
                                      check=False)[1].strip() or 0)),
    )

    cur = main_sha
    merged, rejected, regen = [], [], []

    for sha, session, prio, desc in ordered:
        # 当前 main 的「本可覆盖内容」的优先级基准＝main 上无署名内容 → unmarked
        base_prio = cfg["unmarked_priority"]

        mt = merge_tree(repo, None, cur, sha)   # base 由 merge-tree 自选
        if mt["rc"] == 0:
            # 无冲突 ⇒ 直接采用合并树
            cur = _commit(repo, mt["tree"], cur, sha,
                          "merge(arb): %s [%s p=%d]" % (commit_subject(repo, sha), session, prio))
            merged.append((sha, session, prio, desc, []))
            log("  ✅ %s [%s p=%d] 无冲突合入 → %s" % (sha[:8], session, prio, cur[:8]))
            continue

        conflicts = mt["conflicts"]
        gen_conf = [c for c in conflicts if is_generated(cfg, c)]
        real_conf = [c for c in conflicts if c not in gen_conf]

        # 生成物豁免：先按 ours 版本消掉这些冲突
        tree_fixed = mt["tree"]
        if gen_conf:
            ours_tree = rev(repo, cur + "^{tree}")
            try:
                tree_fixed = apply_ours_on_conflict(repo, mt["tree"], ours_tree, gen_conf)
            except Exception as e:
                log("  ⚠️ 生成物豁免失败（%s），转人工：%s" % (e, sha[:8]))
                rejected.append((sha, session, prio, desc, conflicts, "生成物豁免失败"))
                continue
            regen.extend(gen_conf)

        if not real_conf:
            cur = _commit(repo, tree_fixed, cur, sha,
                          "merge(arb): %s [%s p=%d]" % (commit_subject(repo, sha), session, prio))
            merged.append((sha, session, prio, desc, gen_conf))
            log("  ✅ %s [%s p=%d] 冲突仅生成物(%d)，豁免后合入 → %s"
                % (sha[:8], session, prio, len(gen_conf), cur[:8]))
            continue

        if prio > base_prio:
            # 高优先级胜：用 -X ours 消冲突，但**先留证**（已在上文记下 real_conf）
            mt2 = merge_tree(repo, None, cur, sha, strategy="ours")
            if mt2["rc"] != 0:
                log("  ⛔ %s [%s p=%d] -X ours 仍失败 ⇒ 转人工" % (sha[:8], session, prio))
                rejected.append((sha, session, prio, desc, real_conf, "-X ours 仍失败"))
                continue
            tree2 = mt2["tree"]
            if gen_conf:
                ours_tree = rev(repo, cur + "^{tree}")
                try:
                    tree2 = apply_ours_on_conflict(repo, tree2, ours_tree, gen_conf)
                except Exception as e:
                    log("  ⚠️ 生成物豁免失败：%s" % e)
            cur = _commit(repo, tree2, cur, sha,
                          "merge(arb): %s [%s p=%d, 胜出覆盖 %d 冲突]"
                          % (commit_subject(repo, sha), session, prio, len(real_conf)))
            merged.append((sha, session, prio, desc, gen_conf + real_conf))
            log("  ✅ %s [%s p=%d] 优先级 %d>%d ⇒ 胜出，覆盖 %d 个冲突路径 → %s"
                % (sha[:8], session, prio, prio, base_prio, len(real_conf), cur[:8]))
        else:
            # ⭐ 低优先级触碰了高优先级/既有内容 ⇒ **拒绝**（这就是「能拒绝」）
            rejected.append((sha, session, prio, desc, real_conf, "低优先级不得覆盖"))
            log("  ⛔ %s [%s p=%d] 冲突 %s 且 p=%d ≤ 基准 %d ⇒ 拒绝，转人工"
                % (sha[:8], session, prio, real_conf[:3], prio, base_prio))

    return cur, merged, rejected, sorted(set(regen))


def _commit(repo, tree, p1, p2, msg):
    rc, out, err = git("commit-tree", tree, "-p", p1, "-p", p2,
                       repo=repo, check=False, input_bytes=msg.encode("utf-8"))
    if rc != 0:
        raise RuntimeError("commit-tree 失败：%s" % err)
    return out.strip()


# ---------------------------------------------------------------------- selfcheck
def selfcheck():
    """在临时夹具里逐类篡改输入，证明判据**能拒绝**（红线 10）。"""
    import shutil
    import tempfile
    base = tempfile.mkdtemp(prefix="jvs-arb-sc-")
    results = []
    try:
        cfg = {"unmarked_priority": 40, "default_priority": 10, "ad-hoc": 10,
               "generated_prefixes": ["_index/"],
               "generated_files": ["_handoff/PENDING.md"],
               "sessions": {"daily-dev": {"priority": 50, "desc": "d"},
                            "ad-hoc": {"priority": 10, "desc": "a"}}}
        # ① 低优先级覆盖高优先级 ⇒ 必须拒绝
        r = _sc_case(base, cfg, main_prio=60, cand_session="ad-hoc", cand_prio=10,
                     conflict=True)
        results.append(("①低优先级企图覆盖高优先级", r["rejected"] >= 1, r["log"]))
        # ② 高优先级覆盖低优先级 ⇒ 必须放行
        r = _sc_case(base, cfg, main_prio=10, cand_session="daily-dev", cand_prio=50,
                     conflict=True)
        results.append(("②高优先级覆盖低优先级", r["merged"] >= 1, r["log"]))
        # ③ 无冲突 ⇒ 放行
        r = _sc_case(base, cfg, main_prio=40, cand_session="ad-hoc", cand_prio=10,
                     conflict=False)
        results.append(("③无冲突低优先级", r["merged"] >= 1, r["log"]))
        # ④ 生成物冲突（**仅**生成物）⇒ 豁免放行且列入 regen
        r = _sc_case(base, cfg, main_prio=60, cand_session="ad-hoc", cand_prio=10,
                     conflict=True, gen=True, gen_only=True)
        results.append(("④生成物冲突豁免", r["merged"] >= 1 and r["regen"] >= 1, r["log"]))
        # ⑤ 低优先级方「只碰生成物」但同批夹带真冲突 ⇒ 仍须拒绝（豁免不可被滥用）
        r = _sc_case(base, cfg, main_prio=60, cand_session="ad-hoc", cand_prio=10,
                     conflict=True, gen=True, gen_only=False)
        results.append(("⑤夹带真冲突的低优先级", r["rejected"] >= 1, r["log"]))
        # ⑥ ⭐ stale-tree 回归：main 已推进（新增 mainonly.txt），候选基于旧 base
        #    ⇒ 仲裁后 main 上的新改动**必须仍在**（用候选树合并会静默丢掉它）
        r = _sc_case(base, cfg, main_prio=10, cand_session="daily-dev", cand_prio=50,
                     conflict=False)
        results.append(("⑥main 新改动不回退（stale-tree 回归）", r["kept_mainonly"], r["log"]))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("=" * 70)
    print("仲裁器「能拒绝」负向自证（红线 10）")
    print("=" * 70)
    ok = 0
    for name, passed, detail in results:
        print("  %s %s" % ("✅" if passed else "❌", name))
        if not passed:
            print("      %s" % detail)
        ok += 1 if passed else 0
    print("  ⇒ %d/%d 通过" % (ok, len(results)))
    return 0 if ok == len(results) else 1


def _sc_case(base, cfg, main_prio, cand_session, cand_prio, conflict, gen=False,
             gen_only=False):
    """造一个临时仓库并跑一次仲裁；返回 {merged, rejected, regen, log}。

    gen_only=True：主侧与候选侧**都只**改生成物（`_index/a.json`），不动 shared.txt。
    """
    import shutil
    import tempfile
    tag = "%s_%s_%s_%s_%s_%s_%s" % (main_prio, cand_session, cand_prio, conflict, gen, gen_only,
                                    len(os.listdir(base)) if os.path.isdir(base) else 0)
    repo = os.path.join(base, "r_" + re.sub(r"[^A-Za-z0-9_.-]", "_", tag))
    shutil.rmtree(repo, ignore_errors=True)
    os.makedirs(repo)
    def g(*a, **kw):
        return git(*a, repo=repo, **kw)
    g("init", "-q", ".")
    g("config", "user.email", "t@t"); g("config", "user.name", "t")

    def blobs():
        """把工作区当前内容写成一个树（--all 语义，含删除）。"""
        g("add", "-A")
        return g("write-tree")[1].strip()

    def commit_on(tree, parent, msg):
        args = ["commit-tree", tree]
        if parent:
            args += ["-p", parent]
        rc, out, err = git(*args, repo=repo, check=False,
                           input_bytes=msg.encode("utf-8"))
        if rc != 0:
            raise RuntimeError("commit-tree 失败：%s" % err)
        return out.strip()

    # ① base 提交（用 plumbing，绕开 pre-commit 钩子）
    open(os.path.join(repo, "shared.txt"), "w").write("l1\nl2\nl3\n")
    if gen:
        os.makedirs(os.path.join(repo, "_index"), exist_ok=True)
        open(os.path.join(repo, "_index", "a.json"), "w").write("base\n")
    base_sha = commit_on(blobs(), None, "base")

    # ② main 侧：改 shared（+ 可选改生成物），署名为高/低优先级
    mp = "gate-signoff" if main_prio >= 60 else "ad-hoc"
    if not gen_only:
        open(os.path.join(repo, "shared.txt"), "w").write("l1\nl2-MAIN\nl3\n")
    if gen:
        open(os.path.join(repo, "_index", "a.json"), "w").write("main\n")
    open(os.path.join(repo, "mainonly.txt"), "w").write("m\n")
    main_sha = commit_on(blobs(), base_sha,
                         "main: 改 shared\n\n%s: %s\n" % (SESSION_TRAILER, mp))
    g("update-ref", "refs/heads/main", main_sha)
    g("symbolic-ref", "HEAD", "refs/heads/main")

    # ③ 候选侧：从 base 出发改 shared（冲突）或新增文件（无冲突）
    open(os.path.join(repo, "shared.txt"), "w").write("l1\nl2\nl3\n")   # 先还原 base 态
    if gen:
        open(os.path.join(repo, "_index", "a.json"), "w").write("base\n")
    if os.path.exists(os.path.join(repo, "mainonly.txt")):
        os.remove(os.path.join(repo, "mainonly.txt"))
    if conflict and not gen_only:
        open(os.path.join(repo, "shared.txt"), "w").write("l1\nl2-CAND\nl3\n")
        if gen:
            open(os.path.join(repo, "_index", "a.json"), "w").write("cand\n")
    elif gen:
        open(os.path.join(repo, "_index", "a.json"), "w").write("cand\n")
    else:
        open(os.path.join(repo, "candonly.txt"), "w").write("c\n")
    cand = commit_on(blobs(), base_sha,
                     "cand\n\n%s: %s\n" % (SESSION_TRAILER, cand_session))

    logs = []
    new_main, merged, rejected, regen = arbitrate(
        repo, main_sha, [(cand, cand_session, cand_prio, "")], cfg, log=logs.append)

    # ⭐ stale-tree 回归判据：仲裁结果树里 main 侧新增的 mainonly.txt 是否仍在
    kept_mainonly = False
    if new_main:
        rc, out, _ = git("ls-tree", "-r", "--name-only", "-z", new_main, repo=repo, check=False)
        kept_mainonly = "mainonly.txt" in out.split("\x00")
    return {"merged": len(merged), "rejected": len(rejected), "regen": len(regen),
            "kept_mainonly": kept_mainonly, "new_main": new_main, "log": "\n".join(logs)}


# ---------------------------------------------------------------------- driver
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的推进 main（CAS）")
    ap.add_argument("--dry-run", action="store_true", help="只探测（默认）")
    ap.add_argument("--only", default=None, help="只听从一个 ref/规则")
    ap.add_argument("--refs", default=None, help="逗号分隔的候选 ref 名（默认扫 refs/heads/*）")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()

    if args.selfcheck:
        return selfcheck()

    cfg = load_priority()
    main_sha = rev(JVS, "main")
    if not main_sha:
        print("✗ 找不到 main"); return 1
    print("main = %s" % main_sha[:12])

    # 收集候选
    if args.refs:
        names = [r.strip() for r in args.refs.split(",") if r.strip()]
    else:
        rc, out, _ = git("for-each-ref", "--format=%(refname:short)", "refs/heads/", check=False)
        names = [n for n in out.splitlines() if n and n != "main"]

    cands = []
    for n in names:
        sha = rev(JVS, n)
        if not sha:
            continue
        if git("merge-base", "--is-ancestor", sha, main_sha, check=False)[0] == 0:
            continue  # 已并入
        sess = commit_session(JVS, sha)
        prio, desc = priority_of(cfg, sess)
        cands.append((sha, sess or "ad-hoc", prio, "%s(%s)" % (n, desc)))
        print("  候选 %-28s %s  session=%-16s p=%d" % (n, sha[:8], sess or "(无)", prio))

    if not cands:
        print("⇒ 无待合并候选"); return 0

    new_main, merged, rejected, regen = arbitrate(JVS, main_sha, cands, cfg)

    print("-" * 70)
    print("合入 %d ｜ 拒绝 %d ｜ 待重生成 %d" % (len(merged), len(rejected), len(regen)))
    if regen:
        print("待重生成（须跑六步生成器）:")
        for p in regen:
            print("   -", p)
    if rejected:
        print("⛔ 需人工处理的候选:")
        for sha, sess, prio, desc, conf, why in rejected:
            print("   - %s [%s p=%d] %s" % (sha[:8], sess, prio, why))
            for c in conf[:5]:
                print("       ·", c)
        print("   ⇒ 低优先级不得覆盖高优先级内容；请提升优先级或人工裁定后重跑。")

    if not args.apply:
        print("（dry-run：未改任何 ref；加 --apply 才推进 main）")
        return 3 if rejected else 0

    if rejected:
        print("✗ 有拒绝项 ⇒ 不推进 main（避免夹带半成品）")
        return 3
    if new_main == main_sha:
        print("⇒ 无变化"); return 0

    # CAS 推进
    rc, out, err = git("update-ref", "refs/heads/main", new_main, main_sha, check=False)
    if rc != 0:
        print("✗ CAS 失败（main 已被并发推进）：%s" % err)
        return 1
    print("✅ main 已推进：%s → %s" % (main_sha[:8], new_main[:8]))
    return 0


if __name__ == "__main__":
    sys.exit(main())