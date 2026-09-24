#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""对比 v1r2 与 v1r4 判分,检测 judge 限流污染。"""
import io
import json

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"


def load_items(path):
    with io.open(path, encoding="utf-8") as f:
        s = json.load(f)
    return {i["qid"]: i for i in s.get("items", [])}


def ok(item):
    return bool(item.get("available") and item.get("cause") and item.get("effect") and item.get("evidence_hint"))


def main():
    r2 = load_items(BASE + r"\m1_10_v1r2\scores-sn.json")
    r4 = load_items(BASE + r"\m1_10_v1r4\scores-sn.json")
    n_polluted = 0
    n_real_fail = 0
    print(f"{'qid':6} {'v1r2':22} {'v1r4':22} 判定")
    for qid in sorted(r2):
        a, b = r2[qid], r4[qid]
        av2 = f"{'可用' if a['available'] else '不可用'} {a['cause']}/{a['effect']}/{a['evidence_hint']}"
        av4 = f"{'可用' if b['available'] else '不可用'} {b['cause']}/{b['effect']}/{b['evidence_hint']}"
        tag = ""
        if ok(a) and not b.get("available"):
            tag = "← 判分不可用(v1r2对)"
            n_polluted += 1
        elif ok(a) and not ok(b):
            tag = "← 真错或漂移"
            n_real_fail += 1
        print(f"{qid:6} {av2:22} {av4:22} {tag}")
    print(f"\n疑似污染(判分不可用)={n_polluted} | 真错/漂移={n_real_fail}")


if __name__ == "__main__":
    raise SystemExit(main())
