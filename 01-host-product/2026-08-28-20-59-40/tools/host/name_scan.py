#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""真名扫描闸门:发送前哨兵——扫描文件/目录中是否残留已知真名/别名。

用法:
  python name_scan.py --check <payload.txt | dump.jsonl | dir>
  python name_scan.py --names <names.json>          # 自定义姓名表(默认内置表+identities-init.json)
返回码:0=干净;1=命中(输出命中行摘录)。

内置表:身份表(canonical_name/aliases) + 业务昵称别名(Z-UNMAPPED/Z18/Z01/Z-ALIAS/Z-UNMAPPED)。
"""
import argparse
import io
import json
import os
import re
import sys

BASE_NAMES = {
    "Z01": ["Z01", "Z-ALIAS"], "Z02": [], "Z03": [], "Z04": [], "Z06": [],
    "Z07": [], "Z09": [], "Z10": [], "Z11": [], "Z12": [], "Z14": [],
    "Z15": [], "Z16": [], "Z17": [], "Z18": ["Z18"], "Z26": [], "Z30": [],
    "Z31": [], "Z46（Z46）": ["Z46"], "Z38": [], "Z32": [], "Z33": [],
    "Z35": [], "Z36": [], "Z37": [], "Z39": [], "Z40": [], "Z41": [],
    "Z42": [], "Z43": [], "Z44": [], "Z45": [], "Z47": [], "Z48": [],
    "Z49": [], "Z50": [], "Z52": [], "Z53": [], "Z54": [], "Z55": [],
    "Z57": [], "Z58": [], "Z59": [], "Z60": [], "Z61": [], "Z62": [],
    "Z63": [], "Z64": [], "Z65": [], "Z66": [], "Z67": [], "Z68": [],
    "Z69": [], "Z70": [], "Z-UNMAPPED": ["Z-UNMAPPED"], "Z-UNMAPPED": [], "陈": [],
}


def load_names(extra=""):
    names = dict(BASE_NAMES)
    if extra:
        with io.open(extra, encoding="utf-8") as f:
            data = json.load(f)
        for it in data.get("identities", []):
            cn = it.get("canonical_name")
            if cn and len(cn) >= 2 and not _is_non_person(it):
                names.setdefault(cn, [])
                names[cn].extend(it.get("aliases", []))
    return names


# 占位符 canonical_name 前缀:identities-init 中以「Z19」等命名的临时条目
# (note 明示「真名未取得,待人工补全」),本身不是真名,不构成「残留真名」判据。
_PLACEHOLDER_PREFIXES = ("未映射", "未识别", "未知", "待补全")


def _is_non_person(identity):
    """基于 identities 条目的**结构化字段**判定其是否应排除出真名扫描词表。

    只认结构化证据,不按名称子串猜测——漏报真名是安全风险,故排除须有据:
      ① canonical_name 为占位符(如「Z19」:真名未取得,待人工补全);
      ② note 明示非自然人(如「Z34」:无凭据系统号,排除出人物归一)。
    纯英文别名(如 Z46)不排除——它确为真人别名。
    """
    cn = str(identity.get("canonical_name") or "").strip()
    if any(cn.startswith(p) for p in _PLACEHOLDER_PREFIXES):
        return True
    note = str(identity.get("note") or "")
    if "非自然人" in note or "人物归一" in note:
        return True
    return False


def build_pattern(names):
    # 全名(最长优先)+ 别名;单字名(如"陈")与 Z 码(如 Z02)不参与(误杀/无意义)。
    # 占位符与非自然人条目已由 load_names 按结构化字段事先剔除。
    terms = set()
    for nm, als in names.items():
        if len(nm) >= 2 and not re.fullmatch(r"[A-Z]\d+", nm):
            terms.add(nm)
        for a in als:
            if len(a) >= 2 and not re.fullmatch(r"[A-Z]\d+", a):
                terms.add(a)
    pat = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(pat)


def scan_text(text, pat, names=None):
    hits = []
    for m in pat.finditer(text):
        s = m.group(0)
        line_no = text[:m.start()].count("\n") + 1
        # 排除出现在映射表头/代码注释类的豁免(简单豁免:行含 '= ' 且附近是 '| ' 表行)
        hits.append((line_no, s))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", required=True)
    ap.add_argument("--names", default="")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    names = load_names(args.names)
    pat = build_pattern(names)

    hits = []
    path = args.check
    failures = 0
    if os.path.isdir(path):
        targets = []
        try:
            def on_walk_error(_error):
                nonlocal failures
                failures += 1
            for root, dirs, files in os.walk(path, onerror=on_walk_error):
                dirs[:] = sorted(d for d in dirs if not d.startswith("~$"))
                for name in sorted(files):
                    if name.lower().endswith((".txt", ".md", ".jsonl", ".json")) and not name.startswith("~$"):
                        targets.append(os.path.join(root, name))
        except OSError:
            failures += 1
    elif os.path.isfile(path):
        targets = [path]
    else:
        targets = []
        failures += 1
    for f in targets:
        try:
            text = io.open(f, encoding="utf-8", errors="replace").read()
        except (OSError, UnicodeError):
            failures += 1
            continue
        for (ln, term) in scan_text(text, pat):
            hits.append((f, ln, term))

    checked = len(targets)
    if args.verbose or failures or not checked:
        print(f"[闸门-统计] 检查 {checked} 个文件，失败 {failures} 个")
    if hits:
        print(f"[闸门-拦截] 命中 {len(hits)} 处:")
        seen = set()
        for f, ln, term in hits[:40]:
            key = (os.path.basename(f), ln, term)
            if key in seen:
                continue
            seen.add(key)
            print(f"  {os.path.basename(f)}:{ln} -> {term}")
        if len(hits) > 40:
            print(f"  …共 {len(hits)} 处(前 40 已列)")
        return 1
    if failures or not checked:
        print("[闸门-失败] 输入或读取错误，拒绝放行")
        return 2
    print("[闸门-放行] 未发现残留真名/别名")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
