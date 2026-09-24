#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""合入人工复核结论到两模型审计(按批/对键),生成编造审计汇总。

输入:
  - s4_audit_two-model.jsonl   (两模型判定 + 事件对)
  - review-52-关键对.csv.bak-20260902  (旧版52条,用户已复核:51 N + 1 疑似→WorkBuddy取N)
    ※ 2026-09-20 起位于归档区 _archive/bak-2026-09/（见下方 ARCHIVE 常量）
  - review-15-补审.csv/.md      (新增15条,待补审)
输出:编造审计汇总 md(csv 也可)。
"""
import io
import json
import os

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"

# ⚠️ 2026-09-20：`review-52-关键对.csv.bak-20260902` 已迁入归档区
#    （REFACTOR-PLAN §六 阶段 B / item 1），不再位于 BASE 下。
#    归档**保留原始相对路径**，故按「_archive/bak-2026-09/01-host-product/<原相对路径>」拼回。
#    若仍按 BASE 拼，`os.path.exists` 会静默为假 ⇒ 「52 条人工复核结论（51 N + 1 疑似）」
#    被无声丢弃、汇总结果失真。故此处改为**显式断言**，缺失即报错。
ARCHIVE = (r"C:\Users\<user>\Desktop\JVS\_archive\bak-2026-09"
           r"\01-host-product\2026-08-28-20-59-40\docs\host_memory_dump")


def load_jsonl(path):
    out = []
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main():
    rows = load_jsonl(BASE + r"\s4_audit_two-model.jsonl")
    # 旧版 52 条:批/对 键 → 人工判定(全部 N,WorkBuddy 取证排除 1 疑似)
    # 2026-09-20 起该留档在归档区（见文件头 ARCHIVE 注释），故改从 ARCHIVE 取。
    bak = ARCHIVE + r"\review-52-关键对.csv.bak-20260902"
    reviewed = set()
    assert os.path.exists(bak), f"归档留档缺失，无法按键合入 52 条人工复核结论：{bak}"
    with io.open(bak, encoding="utf-8-sig") as f:
        header = None
        for ln in f:
            ln = ln.rstrip("\n")
            if header is None:
                header = ln
                continue
            cells = ln.split(",")
            if len(cells) >= 3:
                try:
                    b = int(cells[1])
                    p = int(cells[2])
                except ValueError:
                    continue
                reviewed.add((b, p))

    # 15 条补审(批/对)
    NEW15 = [(1,48),(1,52),(2,62),(2,75),(4,181),(4,190),(4,194),(5,294),
             (6,336),(6,319),(10,555),(12,717),(12,690),(12,708),(12,696)]

    # 分类
    by_key = {(r["batch_no"], r["pair_no_global"]): r for r in rows}
    # 关键对 = 分歧 + 单判
    def flag_of(r):
        v5, v6 = r.get("v5"), r.get("v6")
        if v5 and v6:
            return "一致" if (v5.get("strength") == v6.get("strength")
                              and v5.get("type") == v6.get("type")) else "分歧"
        return "单判" if (v5 or v6) else "未判"
    critical_keys = {k for k, r in by_key.items() if flag_of(r) in ("分歧", "单判")}
    n_reviewed = sum(1 for k in reviewed if k in by_key)
    n_15 = sum(1 for k in NEW15 if k in by_key)
    # 关键对里,既非已复核也非新增15 = 真·待补审(应=0,因旧52+新15=67=全部关键对)
    n_pending = len(critical_keys - reviewed - set(NEW15))
    n_unjudged = sum(1 for k, r in by_key.items() if flag_of(r) == "未判")
    n_agree = sum(1 for k, r in by_key.items() if flag_of(r) == "一致")

    md = io.open(BASE + r"\编造审计汇总-20260902.md", "w", encoding="utf-8")
    md.write("# 编造边审计汇总(2026-09-02)\n\n")
    md.write("## 关键对(分歧+单判)编造判定\n\n")
    md.write(f"- 已复核(旧52条,按批/对键):**{n_reviewed} 条 全部 N(0 编造)**\n")
    md.write(f"  - 含 WorkBuddy 取证排除的 1 条疑似(批9/对496 与 批9/对519,evidence 逐字比对 = 非编造)\n")
    md.write(f"- 新增分歧(flag 修复后升入):**{n_15} 条待补审**(清单见 `review-15-补审.md`)\n")
    md.write(f"- 其余关键对:待补审 {n_pending} 条(不应 >0,核对)\n\n")
    md.write("## 未判(81 条)\n\n- 维持批量抽看策略(两模型都未判边 → 无编造风险)\n\n")
    md.write("## 结论(暂定)\n\n")
    md.write("- **主判据「编造边 0」:已复核部分成立(0/52);15 条补审后确认最终结论**\n")
    md.write("- 若 15 条补审也全 N → 编造边 = 0,主判据通过,可定正式图库\n")
    md.close()
    print(f"合入:已复核 {n_reviewed} 条 N | 待补审 15 条 | 其余关键对 {n_pending}")
    print("→ 编造审计汇总-20260902.md")


if __name__ == "__main__":
    raise SystemExit(main())
