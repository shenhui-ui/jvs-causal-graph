#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""真名扫描闸门:发送前哨兵——扫描文件/目录中是否残留已知真名/别名。

用法:
  python name_scan.py --check <payload.txt | dump.jsonl | dir> --names <names.json>
  python name_scan.py --check <dir> --names-dir <dir>   # 读目录下所有 *.json 词表并合并
返回码:0=干净;1=命中(输出命中行摘录);2=输入或词表错误(拒绝放行)。

⭐ 词表必须**外部提供**（本文件不含任何真实姓名）。
   词表格式（两种皆可，可混用）:
     A) {"identities": [{"canonical_name": "...", "aliases": ["..."], ...}, ...]}
     B) {"<姓名>": ["<别名1>", "<别名2>"], ...}
   结构见同目录 `names-example.json`（**虚构**示例）。

⚠️ **未提供词表 / 词表为空 ⇒ 本脚本拒绝运行并返回 2**。
   理由:「空词表 ⇒ 扫描通过」会输出**虚假的干净结论** —— 这比报错危险得多。
"""

import argparse
import io
import json
import os
import re
import sys

# ⭐ 公开版**不内置任何真实姓名**。
#    原版把 57 个真实姓名硬编码在此处 ⇒ 「公开代码即泄露姓名」。
#    现改为从 `--names` / `--names-dir` 读入（见 load_names）。
BASE_NAMES = {}


def _is_non_person(identity):
    """基于 identities 条目的**结构化字段**判定其是否应排除出真名扫描词表。

    只认结构化证据,不按名称子串猜测——漏报真名是安全风险,故排除须有据:
      ① canonical_name 为占位符(如「未映射-01」:真名未取得,待人工补全);
      ② note 明示非自然人(如「无凭据系统号,排除出人物归一」)。
    纯英文别名不排除——它确为真人别名。
    """
    cn = str(identity.get("canonical_name") or "").strip()
    if any(cn.startswith(p) for p in _PLACEHOLDER_PREFIXES):
        return True
    note = str(identity.get("note") or "")
    if "非自然人" in note or "人物归一" in note:
        return True
    return False


# 占位符 canonical_name 前缀(以「待补全」等命名的临时条目),
# 本身不是真名,不构成「残留真名」判据。
_PLACEHOLDER_PREFIXES = ("未映射", "未识别", "未知", "待补全")


def _merge_into(names, obj):
    """把一份词表对象并入 names（dict: 姓名 -> [别名...]）。"""
    if not isinstance(obj, dict):
        return
    # 格式 A：{"identities": [...]}
    for it in (obj.get("identities") or []):
        if not isinstance(it, dict):
            continue
        cn = str(it.get("canonical_name") or "").strip()
        if cn and len(cn) >= 2 and not _is_non_person(it):
            names.setdefault(cn, [])
            names[cn].extend([a for a in (it.get("aliases") or []) if isinstance(a, str)])
    # 格式 B：{"姓名": [别名...]}（跳过 identities 键自身）
    for k, v in obj.items():
        if k == "identities" or not isinstance(k, str):
            continue
        if len(k) < 2:
            continue
        names.setdefault(k, [])
        if isinstance(v, list):
            names[k].extend([a for a in v if isinstance(a, str)])


def load_names(names_file="", names_dir=""):
    """从外部文件/目录读入真名词表。

    ⚠️ 词表缺失或为空 ⇒ 主动 `SystemExit`，**不返回空模式**。
    """
    names = dict(BASE_NAMES)
    loaded = []

    def _read(path):
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)

    if names_file:
        if not os.path.isfile(names_file):
            raise SystemExit("[闸门-致命] 词表文件不存在: %s" % names_file)
        _merge_into(names, _read(names_file))
        loaded.append(names_file)

    if names_dir:
        if not os.path.isdir(names_dir):
            raise SystemExit("[闸门-致命] 词表目录不存在: %s" % names_dir)
        for fn in sorted(os.listdir(names_dir)):
            if fn.lower().endswith(".json"):
                _merge_into(names, _read(os.path.join(names_dir, fn)))
                loaded.append(os.path.join(names_dir, fn))

    if not loaded:
        raise SystemExit(
            "[闸门-致命] 未提供词表 —— 拒绝运行。\n"
            "  请用 --names <file> 或 --names-dir <dir> 指定词表（结构见 names-example.json）。\n"
            "  ⚠️ 空词表会输出**虚假的干净结论**，故本脚本选择报错而不是放行。")
    return names


def build_pattern(names):
    # 全名(最长优先)+ 别名;单字名与纯代号(如 Z02)不参与(误杀/无意义)。
    # 占位符与非自然人条目已由 load_names 按结构化字段事先剔除。
    terms = set()
    for nm, als in names.items():
        if len(nm) >= 2 and not re.fullmatch(r"[A-Z]\d+", nm):
            terms.add(nm)
        for a in als:
            if len(a) >= 2 and not re.fullmatch(r"[A-Z]\d+", a):
                terms.add(a)
    if not terms:
        raise SystemExit(
            "[闸门-致命] 词表读入成功但**有效词条为 0** —— 拒绝运行。\n"
            "  ⚠️ 用空模式去扫描会输出**虚假的干净结论**。")
    pat = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(pat)


def scan_text(text, pat, names=None):
    hits = []
    for m in pat.finditer(text):
        s = m.group(0)
        line_no = text[:m.start()].count("\n") + 1
        hits.append((line_no, s))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", required=True)
    ap.add_argument("--names", default="", help="词表文件（JSON）")
    ap.add_argument("--names-dir", default="", help="词表目录（合并其中所有 *.json）")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    names = load_names(args.names, args.names_dir)
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