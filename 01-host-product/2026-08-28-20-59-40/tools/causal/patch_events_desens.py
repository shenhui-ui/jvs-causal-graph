#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""W3 隐私补丁:all-real-events.jsonl 文本字段残留真名/别名 → 官方 Z 码。

映射来源(单一权威):incoming_real\identities-v1.json(66 身份,W2 定稿,用户裁决记录在 review_note);
昵称桥接:name_scan.BASE_NAMES 的别名表(Z01/Z-ALIAS/Z18/Z-UNMAPPED/Z46 → 对应真名 → 同码)。
无 Z 码身份(P056 Z-UNMAPPED / P066 Z-UNMAPPED,用户裁决"保持待确认")用其 P-id 作占位码。
只改字符串值(最长优先、单趟替换);event_id/事件数/结构不变,末尾自检 393 与 id 集合一致。
"""
import io
import json
import re
import shutil
import sys

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40"
EVENTS = BASE + r"\docs\host_memory_dump\all-real-events.jsonl"
IDENT = BASE + r"\docs\host_memory_dump\incoming_real\identities-v1.json"
BACKUP = BASE + r"\docs\host_memory_dump\all-real-events.preW3-desens-20260831.jsonl"
sys.path.insert(0, BASE + r"\tools\host")
from name_scan import BASE_NAMES  # noqa: E402  (复用闸门同一张表,防漏)


def build_map():
    data = json.load(io.open(IDENT, encoding="utf-8"))
    term2code = {}
    for it in data["identities"]:
        cn = it.get("canonical_name") or ""
        zcodes = [a for a in it.get("aliases", []) if re.fullmatch(r"Z\d+", a)]
        code = zcodes[0] if zcodes else it["id"]  # 无 Z 码 → P-id 占位
        if len(cn) >= 2:
            term2code[cn] = code
        for a in it.get("aliases", []):
            if re.fullmatch(r"Z\d+", a):
                continue  # 本身是码,不替换
            if len(a) >= 2 and a != cn:
                term2code.setdefault(a, code)
    # 昵称桥接:BASE_NAMES[真名] = [昵称...] → 真名的码
    for real, nicks in BASE_NAMES.items():
        code = term2code.get(real)
        if not code:
            continue
        for nick in nicks:
            if len(nick) >= 2:
                term2code.setdefault(nick, code)
    return term2code


def sub_value(v, pat, term2code, counter):
    if isinstance(v, str):
        def rep(m):
            counter[term2code[m.group(0)]] = counter.get(term2code[m.group(0)], 0) + 1
            return term2code[m.group(0)]
        return pat.sub(rep, v)
    if isinstance(v, list):
        return [sub_value(x, pat, term2code, counter) for x in v]
    if isinstance(v, dict):
        return {k: sub_value(x, pat, term2code, counter) for k, x in v.items()}
    return v


def main():
    term2code = build_map()
    terms = sorted((t for t in term2code if len(t) >= 2), key=len, reverse=True)
    pat = re.compile("|".join(re.escape(t) for t in terms))
    print("映射条目:%d(码种:%d)" % (len(terms), len(set(term2code.values()))))

    orig_ids, counter, out_lines = [], {}, []
    with io.open(EVENTS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            orig_ids.append(obj["event_id"])
            out_lines.append(json.dumps(sub_value(obj, pat, term2code, counter), ensure_ascii=False))

    assert len(out_lines) == 393, "事件数变了:%d" % len(out_lines)
    new_ids = [json.loads(l)["event_id"] for l in out_lines]
    assert new_ids == orig_ids, "event_id 集合/顺序变了"

    shutil.copyfile(EVENTS, BACKUP)
    with io.open(EVENTS, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")
    print("备份 → %s" % BACKUP)
    print("替换计数(按码):", json.dumps(counter, ensure_ascii=False, sort_keys=True))
    total = sum(counter.values())
    print("共替换 %d 处;事件 393 条、event_id 顺序未变" % total)


if __name__ == "__main__":
    main()
