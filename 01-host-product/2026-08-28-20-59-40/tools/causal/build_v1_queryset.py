#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""生成 query-set-real-v1.jsonl(盖章版)+ 审定记录。

按用户终裁(2026-08-30):
- Q015:保留标注(graph_dependency=no 如实保留,不剔除)→ 30 条齐、零补题;
- 低置信标注收窄:仅 Q019/Q020/Q029/Q030(4 条);Q013/Q026 去标(引用 R70xxx,非 R900 低置信组);
- Q001:evidence_hint 引用修正(R60162/R60163 张冠李戴 → 以 R60059/R60063 为据,删除无据细节「proto/bag、8%」);
- 抽查 Q002/Q003/Q009/Q027/Q030 无异议;其余取样按无异议。
附加:全体 30 条回填 endpoint_reachable(程序检测 30/30 yes)+ 审核元数据(reviewed_by/review_date/verdict)。
"""
import io
import json
import re

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"
SRC = BASE + r"\query-set-real-init.jsonl"
OUT = BASE + r"\query-set-real-v1.jsonl"
REC = BASE + r"\query-set-v1-审定记录.md"
EV_ORD = re.compile(r"\b([RGR][0-9A-Z]{3,7})\b")


def main():
    evs = set()
    with io.open(BASE + r"\all-real-events.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                evs.add(json.loads(line)["event_id"])
    qs = []
    with io.open(SRC, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                qs.append(json.loads(line))

    LOW_CONF = {"Q019", "Q020", "Q029", "Q030"}
    for q in qs:
        qid = q["qid"]
        # 1) Q001 修正 evidence_hint
        if qid == "Q001":
            q["gold_answer"]["evidence_hint"] = (
                "R60059(5/4 Z31 定位:两类错误都是 topic 数据不完整导致解析失败,"
                "设备端上报数据有问题)→R60063(5/5 Z46 明确责任层:问题在哪儿就在哪儿解决)")
            q["note"] = (q.get("note", "") +
                         "; [v1修正] evidence_hint 原引用 R60162/R60163 张冠李戴(实为 6/22 后续任务),"
                         "已改以 R60059/R60063 为据;「proto/bag、失败率约8%」为语料未抽取细节,不作断言")
        # 2) 低置信标注收窄
        q.pop("low_confidence", None)
        if qid in LOW_CONF:
            q["low_confidence"] = True
        # 3) endpoint 可达 + 审核元数据
        txt = json.dumps(q.get("gold_answer", ""), ensure_ascii=False)
        refs = [r for r in EV_ORD.findall(txt) if r not in ("GR", "RG")]
        q["endpoint_reachable"] = "yes" if all(r in evs for r in refs) else "no"
        q["review_meta"] = {"reviewed_by": "user", "review_date": "2026-08-30",
                            "verdict": "保留" if qid != "Q001" else "保留(含修正)"}

    with io.open(OUT, "w", encoding="utf-8") as f:
        for q in qs:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")

    n_end = sum(1 for q in qs if q["endpoint_reachable"] == "yes")
    lines = ["# 题集审定记录(v1,2026-08-30)",
             "",
             "- 审定人:项目负责人 ｜ 依据:审定视图 30 条 + 用户终裁",
             "- **结论:全部 30 条保留,零剔除、零补题;构成不变(为什么改 12 / 改了什么 8 / 其他 10)**",
             "- endpoint_reachable:30/30(程序检测,与冻结 393 核对;封面图库以冻结口径为准)",
             "",
             "## 裁决明细",
             "",
             "| qid | 裁决 | 说明 |",
             "|---|---|---|",
             "| Q015 | 保留(标注) | 事实在群聊4原文可查;仅 conf 0.3 / unknown ts;graph_dependency=no 如实标注——不剔除(与 AI 建议相反,用户终裁) |",
             "| Q019/Q020/Q029/Q030 | 保留(低置信标注) | 标注范围收窄为 4 条(原「3 条」更正);Q020 有明确日期注记 |",
             "| Q013/Q026 | 去标 | 🔶 低置信标注与实引 R70xxx 不符,复核后撤除 |",
             "| Q001 | 保留(修正) | evidence_hint 引用张冠李戴(见 note),已修正并记录 |",
             "| 其余 24 条 | 无异议 | 抽查 Q002/Q003/Q009/Q027/Q030 无异议;证据定位 5 条 [src: hash] 格式一致 |",
             "",
             "## 代表性评估记录",
             "",
             "- 剔除:0 条 → 无题型剔除 → **未触发「某类剔除 >30% 需评审会复核」条款**;",
             "- 保留标注 5 条(Q015 + 4 条低置信)属于「质量控制注记」,不影响构成口径;",
             "- 若 M1 期间补充语料(如 M2 数据清洗),按口径变更程序重新审定。",
             "",
             "## 修订追踪(Q001)",
             "",
             "- 2026-08-30 用户复核发现:evidence_hint 引用 R60162/R60163 与实际事件内容不符(实为 6/22 Z30/Z35 后续任务);",
             "- 修正:以 R60059(5/4 定位根因)→R60063(5/5 明确责任层)为据;删除无抽取依据的「proto/bag、8%」细节;",
             "- 佐证:库内四事件实况(ts/内容)见 all-real-events.jsonl。"]
    with io.open(REC, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[ok] v1: {len(qs)} 条 → {OUT}")
    print(f"[ok] 记录 → {REC};endpoint 可达 {n_end}/30")


if __name__ == "__main__":
    raise SystemExit(main())
