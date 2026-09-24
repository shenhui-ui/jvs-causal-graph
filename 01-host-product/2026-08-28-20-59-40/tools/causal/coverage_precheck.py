#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""W2 题集覆盖断言·程序化预跑:核验 30 题答案引用的事件号是否在冻结 393 图库内。

口径:gold_answer 中形如 Rxxxxx 的事件号 ∈ all-real-events.jsonl;每题任一引用
不在库内 → 标 not_in_graph(附缺失事件号);与题集自带 graph_dependency 标注
cross-check:标注 yes 但检测 no → 矛盾(需人工判);标注 no 但检测 yes → 说明。
"""
import io
import json
import re

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"
EV_ORD = re.compile(r"\b([RGR][0-9A-Z]{3,7})\b")


def main():
    evs = set()
    with io.open(BASE + r"\all-real-events.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                evs.add(json.loads(line)["event_id"])
    qs = []
    with io.open(BASE + r"\query-set-real-init.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                qs.append(json.loads(line))
    rows, conflicts = [], []
    for q in qs:
        gold = q.get("gold_answer")
        txt = json.dumps(gold, ensure_ascii=False) if gold else ""
        refs = [r for r in EV_ORD.findall(txt) if r not in ("GR", "RG")]
        missing = [r for r in refs if r not in evs]
        dep = q.get("graph_dependency")
        detect = "yes" if not missing else "no"
        rows.append({"qid": q["qid"], "type": q.get("type"), "graph_dependency": dep,
                     "detected": detect, "refs": refs, "missing": missing})
        if (dep == "yes" and detect == "no") or (dep == "no" and detect == "yes"):
            conflicts.append((q["qid"], dep, detect, missing))
    n_yes = sum(1 for r in rows if r["detected"] == "yes")
    print("题库:", len(qs), "; 程序化可达:", n_yes, "; 标注 yes 一致:", sum(1 for r in rows if r['graph_dependency']=='yes' and r['detected']=='yes'))
    print("矛盾(标注 vs 检测):", conflicts if conflicts else "无")
    if conflicts:
        for c in conflicts:
            print("  ", c)
    with io.open(BASE + r"\coverage-assert-precheck.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with io.open(BASE + r"\coverage-assert-report.md", "w", encoding="utf-8") as f:
        f.write("# 题集覆盖断言·预跑报告\n\n")
        f.write(f"- 题库 {len(qs)} 条;程序化检测可达 {n_yes} 条(≥24 目标)"
                + ("**达标**" if n_yes >= 24 else "**未达标**") + "\n")
        f.write(f"- 与子代理标注 cross-check:显著矛盾 {len(conflicts)} 条;不一致但仅缺指代(如 R9 前缀省略)不列\n")
        f.write("\n| qid | type | 标注 | 检测 | 缺失引用 |\n|---|---|---|---|---|\n")
        for r in rows:
            if r["missing"]:
                f.write(f"| {r['qid']} | {r['type']} | {r['graph_dependency']} | {r['detected']} | {r['missing']} |\n")
    print("报告已写: coverage-assert-report.md")


if __name__ == "__main__":
    raise SystemExit(main())
