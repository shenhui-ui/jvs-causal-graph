# -*- coding: utf-8 -*-
"""语料路径基线（只增不减）—— 门禁 [5] 的判据源。

为什么需要它（背景）
-------------------
门禁 [5] 原用两次全树 `find` + 阈值（`-ge 2000` / `-ge 30`）判「核心大体积产物仍在」。
实测该阈值**丢 363 个 `.sse`（2,363 → 2,000）仍然全绿** —— 而这正是 GIT-POLICY 红线 1
（「文件被批量移入回收站」事故形态）要保护的对象。

改成「读一份路径清单 + 逐条 os.stat」后有两个必须一起解决的点：
  1. **清单必须比索引更权威** —— 「索引与 HEAD 一致」≠「索引新鲜」。
     若**先删盘、再刷新索引**，该路径已**从索引消失** ⇒ stat 也无事可做 ⇒ 静默漏报。
     ⇒ 故**不用 `_index/文件索引.jsonl` 当清单**，改用本文件：它**只增不减**。
  2. **判据必须能指名缺失文件**，而不是只报一个计数。

本文件即那份清单。生成/更新入口：

    python scripts/corpus-baseline.py update            # 只允许「新增」，任何「消失」一律报错
    python scripts/corpus-baseline.py update --dry-run  # 只看差异，不写盘
    python scripts/corpus-baseline.py check             # 判据（门禁调用；另有根自证）
    python scripts/corpus-baseline.py check --full      # 顺带报「磁盘有·基线无」的未登记项
    python scripts/corpus-baseline.py check --hashes    # 慢路径：逐条重算 sha256（约 4.4 s）

三条铁律
--------
  R1 **基线绝不随索引刷新而缩小** —— 这是本方案唯一的自毁路径。
     条目消失必须显式 `--allow-remove --reason "..."`，且**必须写进提交信息**。
  R2 **基线不带 `--root` 语义** —— 文件内一律存**仓根相对路径**（`/` 分隔），
     解析时 `os.path.join(root, p)`。这样夹具才能按自己的根重新生成一份（否则路径口径对不上）。
  R3 **基线要能自证** —— 首行 meta 含 `entries`，读数与实条不符即判「基线文件损坏/被截断」，
     先怀疑基线本身，而不是先信任它给出的结论。
"""
import argparse
import hashlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.dirname(HERE)                     # scripts/ 的上一级 = 仓根
BASELINE = os.path.join(HERE, "corpus-baseline.jsonl")
SCHEMA = "jvs-corpus-baseline/1"

# 收集口径：与门禁原 `find . -name "*.sse" -not -path "*/.git/*"` **一致** ——
# 只排除 `.git`，不排除 `.workbuddy-ai/` 等（口径若与旧命令不同，计数就对不上，
# 而「计数一致」是 P23-a 的判据之一）。
EXTS = (".sse", ".zip")
SKIP_DIRS = {".git"}
BUF = 1 << 20


# ---------------------------------------------------------------- 基础

def norm_rel(p):
    """统一成仓根相对路径（`/` 分隔）。"""
    return p.replace(os.sep, "/")


def abs_of(root, rel):
    """R2：解析时一律经过 root，绝不把相对路径当绝对路径用。"""
    return os.path.join(root, rel.replace("/", os.sep))


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(BUF)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def scan(root):
    """全树收集 EXTS 文件 ⇒ {相对路径: 字节数}。followlinks=False（与 find 默认一致）。"""
    found = {}
    for dp, dns, fns in os.walk(root, followlinks=False):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if fn.lower().endswith(EXTS):
                fp = os.path.join(dp, fn)
                try:
                    found[norm_rel(os.path.relpath(fp, root))] = os.path.getsize(fp)
                except OSError:
                    pass
    return found


# ---------------------------------------------------------------- 读写

def load(path):
    """⇒ (meta, [(rel, bytes, sha256), ...])；文件不存在 ⇒ (None, [])。"""
    if not os.path.exists(path):
        return None, []
    meta, items = None, []
    with io.open(path, encoding="utf-8") as f:
        for i, ln in enumerate(f):
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except ValueError:
                raise SystemExit("基线文件第 %d 行不是合法 JSON：%s" % (i + 1, ln[:80]))
            if o.get("_meta"):
                meta = o
            else:
                items.append((o["p"], int(o["b"]), o.get("h", "")))
    return meta, items


def dump(meta, items, path):
    """原子落盘（临时文件 + os.replace），避免半截基线。"""
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(meta, ensure_ascii=False, sort_keys=True) + "\n")
        for rel, b, h in items:
            f.write(json.dumps({"p": rel, "b": b, "h": h},
                               ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, path)


def selfcheck(meta, items, label="基线"):
    """R3：基线自证。无 meta 或条数不符 ⇒ 失败（先怀疑清单本身）。"""
    if meta is None:
        return "%s缺少 meta 首行（文件被截断？）" % label
    if meta.get("schema") != SCHEMA:
        return "%sschema 不符：%r（期望 %s）" % (label, meta.get("schema"), SCHEMA)
    if int(meta.get("entries", -1)) != len(items):
        return ("%s自证失败：meta.entries=%s 但实有 %d 条"
                "（文件被截断或被手改？）" % (label, meta.get("entries"), len(items)))
    return None


# ---------------------------------------------------------------- update

def cmd_update(a):
    root = os.path.abspath(a.root)
    disk = scan(root)
    meta, items = load(a.path)
    old = {p: (b, h) for p, b, h in items}

    added = sorted(set(disk) - set(old))
    removed = sorted(set(old) - set(disk))
    changed = sorted(p for p in set(disk) & set(old) if disk[p] != old[p][0])

    print("仓根: %s" % root)
    print("磁盘现测 : %d 条（%s）" % (len(disk), _split(disk)))
    print("基线在册 : %d 条" % len(old))
    print("  新增 %d 条" % len(added))
    print("  消失 %d 条%s" % (len(removed), "" if removed else "  ← R1：本项必须恒为 0"))
    print("  字节数变化 %d 条" % len(changed))
    for p in added[:20]:
        print("    [+] %s" % p)
    for p in removed[:20]:
        print("    [-] %s" % p)
    for p in changed[:20]:
        print("    [~] %s  %d -> %d" % (p, old[p][0], disk[p]))

    if removed and not a.allow_remove:
        print()
        print("❌ 拒绝写入：检测到 %d 条基线条目在磁盘上消失。" % len(removed))
        print("   R1 规定基线**只增不减** —— 除非确认为「经批准的语料变更」，")
        print("   否则这就是红线 1 的事故形态（文件被移入回收站），应当先恢复文件。")
        print("   确需注销请显式：--allow-remove --reason \"...\"，并写进提交信息。")
        return 1

    if not added and not changed and not removed:
        print()
        print("✅ 基线已是最新，无需写入。")
        return 0

    new_items = []
    for p in sorted(set(old) | set(disk)):
        if p in removed:
            continue                                      # 仅 --allow-remove 时可达
        # ⚠️ 一律重算哈希，**不复用旧值** —— 复用只在「字节数未变」时省时间，
        #    而「字节数没变、内容却变了」正是本项目登记过的已知盲区（见 hash_cache 的过期面）。
        #    更新是低频操作（全量 3.5 GB 约 5 s），不值得为省几秒引入一个静默错值。
        new_items.append((p, disk[p], sha256_of(abs_of(root, p))))

    meta_out = {
        "_meta": True,
        "schema": SCHEMA,
        "created": (meta or {}).get("created", a.today),
        "updated": a.today,
        "exts": [e.lstrip(".") for e in EXTS],
        "root_rel": True,
        "entries": len(new_items),
        "note": "只增不减；仓根相对路径；判据见 scripts/corpus-baseline.py check",
    }
    if removed:
        meta_out["last_removal"] = {
            "date": a.today, "reason": a.reason, "count": len(removed),
            "paths": removed,
        }

    if a.dry_run:
        print()
        print("（--dry-run）将写入 %d 条，未落盘。" % len(new_items))
        return 0

    dump(meta_out, new_items, a.path)
    print()
    print("✅ 已写入 %s：%d 条（新增 %d，消失 %d）"
          % (os.path.relpath(a.path, root), len(new_items), len(added), len(removed)))
    if removed:
        print("⚠️ 已注销 %d 条（reason=%s）—— 必须写进提交信息。" % (len(removed), a.reason))
    return 0


def _split(d):
    c = {e: 0 for e in EXTS}
    for p in d:
        for e in EXTS:
            if p.lower().endswith(e):
                c[e] += 1
    return " / ".join("%s %d" % (e.lstrip("."), n) for e, n in c.items())


# ---------------------------------------------------------------- check

def cmd_check(a):
    root = os.path.abspath(a.root)
    meta, items = load(a.path)

    err = selfcheck(meta, items)
    if err:
        print("  [FAIL] %s" % err)
        return 1
    if not items:
        print("  [FAIL] 基线为空 —— 判据无效（无清单可判）")
        return 1

    # 判据 ①：基线中每条路径都必须存在（磁盘真值 = os.stat）
    missing = [p for p, _, _ in items if not os.path.exists(abs_of(root, p))]
    c = {e: 0 for e in EXTS}
    for p, _, _ in items:
        for e in EXTS:
            if p.lower().endswith(e):
                c[e] += 1

    if missing:
        show = missing[: a.max_show]
        for p in show:
            print("  [MISS] %s" % p)
        if len(missing) > len(show):
            print("  [MISS] … 另有 %d 条未列出（--max-show 调整）" % (len(missing) - len(show)))
        print("  [FAIL] 语料基线缺失 %d/%d 条（仓根 %s）" % (len(missing), len(items), root))
        print("         ⇒ 疑似 GIT-POLICY §11 铁律 1 的事故形态（文件被移入回收站）；")
        print("           先按 git-sigterm-safe-recovery 还原，不要改基线。")
        return 1

    print("  [OK]   语料基线 %d 条（%s），缺失 0" % (len(items), _split({p for p, _, _ in items})))

    # 判据 ②（可选，慢）：内容哈希
    if a.hashes:
        bad = []
        for p, b, h in items:
            if not h:
                bad.append((p, "基线无哈希"))
                continue
            if sha256_of(abs_of(root, p)) != h:
                bad.append((p, "内容已变"))
        if bad:
            for p, why in bad[: a.max_show]:
                print("  [HASH] %s  %s" % (p, why))
            print("  [FAIL] 语料内容哈希不符 %d/%d 条" % (len(bad), len(items)))
            return 1
        print("  [OK]   语料内容哈希 %d/%d 逐条一致" % (len(items), len(items)))

    # 判据 ③（可选）：双向 —— 磁盘有但基线未登记 ⇒ WARN（不阻塞）
    if a.full:
        disk = scan(root)
        unreg = sorted(set(disk) - {p for p, _, _ in items})
        if unreg:
            print("  [WARN] 磁盘上有 %d 条未登记进基线（不阻塞）：" % len(unreg))
            for p in unreg[: a.max_show]:
                print("         %s" % p)
            print("         ⇒ 跑 python scripts/corpus-baseline.py update 追加")
        else:
            print("  [OK]   磁盘与基线双向一致（无未登记项）")
    return 0


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="语料路径基线（只增不减）—— 门禁 [5] 的判据源与自证",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("update", "check"))
    ap.add_argument("--root", default=None,
                    help="被检查的根（默认 = 本脚本上一级目录，即仓根）。"
                         "R2：夹具测试必须传自己的根，否则路径口径对不上")
    ap.add_argument("--path", default=BASELINE, help="基线文件（默认 scripts/corpus-baseline.jsonl）")
    ap.add_argument("--dry-run", action="store_true", help="update：只看差异，不写盘")
    ap.add_argument("--allow-remove", action="store_true",
                    help="update：允许注销条目（R1 例外，须配 --reason）")
    ap.add_argument("--reason", default="", help="update：注销理由（--allow-remove 时必填）")
    ap.add_argument("--today", default=None, help="update：写入 meta 的日期（默认系统当天）")
    ap.add_argument("--hashes", action="store_true", help="check：逐条重算 sha256（慢，约 4.4 s）")
    ap.add_argument("--full", action="store_true", help="check：顺带报未登记项（WARN，不阻塞）")
    ap.add_argument("--max-show", type=int, default=15, help="最多打印多少条明细")
    a = ap.parse_args()

    if a.root is None:
        a.root = DEFAULT_ROOT
    if a.today is None:
        import datetime
        a.today = datetime.date.today().isoformat()

    if a.mode == "update":
        if a.allow_remove and not a.reason.strip():
            print("❌ --allow-remove 必须同时给 --reason \"...\"（R1：注销要留痕）")
            return 2
        return cmd_update(a)
    return cmd_check(a)


if __name__ == "__main__":
    sys.exit(main())