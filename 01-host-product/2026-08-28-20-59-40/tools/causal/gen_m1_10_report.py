#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""生成 M1-10 两库基线对照报告(读 m1_10_baseline 与 m1_10_baseline_v6 的 scores.json)。"""
import io
import json

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"


def load_scores(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def metrics(s):
    m = s.get("metrics", {})
    av = m.get("availability", {}).get("available_rate")
    ca = m.get("causal_accuracy", {})
    ca_v = ca.get("causal_accuracy")
    n_corr = ca.get("n_all_correct")
    n_cau = ca.get("n_causal")
    pe = ca.get("per_element", {})
    return {
        "av": av, "ca": ca_v, "n_corr": n_corr, "n_cau": n_cau,
        "cause": pe.get("cause", {}).get("rate"),
        "effect": pe.get("effect", {}).get("rate"),
        "hint": pe.get("evidence_hint", {}).get("rate"),
        "qual": m.get("qualified", {}).get("end_to_end_qualified_rate"),
    }


def main():
    v7 = metrics(load_scores(BASE + r"\m1_10_baseline\scores.json"))
    v6 = metrics(load_scores(BASE + r"\m1_10_baseline_v6\scores.json"))

    md = io.open(BASE + r"\M1-10-两库基线对照-20260902.md", "w", encoding="utf-8")
    md.write("# M1-10 因果准确率基线·两库对照(骨架版,2026-09-02)\n\n")
    md.write("> 口径:决策 A(判分三要素,不依赖图多跳);管线=m1_10_pipeline.py(检索→why→模板,未接 LLM 生成层);\n")
    md.write("> 决策:图库「v5 为主 + v6 对照」(负责人确认);主判据「编造边 0」已通过(67 关键对全 N)。\n\n")
    md.write("| 指标 | **v5 系(v7)** | v6 | 目标 |\n|---|---|---|---|\n")
    md.write(f"| 可用率 | {v7['av']*100:.1f}% | {v6['av']*100:.1f}% | ≥90% |\n")
    md.write(f"| **因果准确率** | **{v7['ca']*100:.1f}%**({v7['n_corr']}/{v7['n_cau']}) | {v6['ca']*100:.1f}%({v6['n_corr']}/{v6['n_cau']}) | 60% 硬/70% 目标 |\n")
    md.write(f"| 合格率 | {v7['qual']*100:.1f}% | {v6['qual']*100:.1f}% | 60% 硬/70% 目标 |\n")
    md.write(f"| cause 命中 | {v7['cause']*100:.1f}% | {v6['cause']*100:.1f}% | — |\n")
    md.write(f"| effect 命中 | {v7['effect']*100:.1f}% | {v6['effect']*100:.1f}% | — |\n")
    md.write(f"| evidence_hint 命中 | {v7['hint']*100:.1f}% | {v6['hint']*100:.1f}% | — |\n\n")
    md.write("## 结论\n\n")
    md.write("- **v5 系(v7)骨架基线 26.7%,显著优于 v6 的 13.3%**——印证「v5 为主」决策;\n")
    md.write("- 骨架版未接 LLM 答案生成层,纯图检索+模板,**这是'地板'不是上限**;\n")
    md.write("- **下一步:加 LLM 答案生成层**(题面+图证据链 → glm-5.2 组织三要素答案)→ 预期拉到 50–70%;\n")
    md.write("- 两库差异归因:v5 强边多(103)检索命中更充分,v6 强边少(57)导致图证据链更短。\n\n")
    md.write("## 挂账\n\n")
    md.write("- 骨架基线如实标注为「未接入 LLM 生成层」;正式基线 = LLM 生成层版(待跑);\n")
    md.write("- 15 条补审 3 处省略号压缩判定,建议人工抽查 1 次收尾(WorkBuddy 已回填 N)。\n")
    md.close()
    print("→ M1-10-两库基线对照-20260902.md")


if __name__ == "__main__":
    raise SystemExit(main())
