#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""按补丁生成 prompt_causal_edges_v2.1.md(在 v2 基础上扩展 strong 判据)。"""
import io

V2 = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\prompts\prompt_causal_edges_v2.md"
OUT = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\prompts\prompt_causal_edges_v2.1.md"

NEW_STRENGTH = """# strength 分级标准(核心新增 · v2.1 扩展)
- **strong** — 满足任一:
  1. 原判据:存在**明确因果/触发/替换句**,可读出「因为 X 所以 Y」「由于 X 导致 Y」「把 A 改为 B」「由 X 改为 Y」「从 X 变成 Y」「替代了 A」「换成/替换/取消」等因果或**变更标志句式**;
  2. **决策→变更衔接**:同一 subject/主线上的 `decision`(明确决定)与后续 `change`(该决定的落实/变更),时间先后、对象一致 → 判 `supersede`(方案/版本替换)或 `cause`(决策句含理由)或 `trigger`,strength=strong;
  3. **变更差分明确**:change 事件(after)自带 before→after 差分且对象明确(如「由 5 秒改为 8 秒」)→ `supersede`,strength=strong。
  **多跳展开只允许走 strong 边。**
- **weak**:保持 v2 —— 两事件存在**可验证语义关联但无明确因果句/变更句式/决策衔接**(同主题补充、细化、报告、指派等),仅用于展示与线索,**不参与多跳展开**。
- 判定顺序:先读是否有上述 strong 证据(句式/差分/决策衔接)→ 有则 strong;再读是否同主题推进 → 有则 weak;两者皆无 → **跳过(不输出边)**,不要为了凑数降成 weak。
"""

TYPE_MAP = """

# 判型映射补充(v2.1)
| 情形 | type | strength |
|---|---|---|
| 原文含「把A改为B / 由X改为Y / 从X变成Y / 替换 / 换成 / 取消」 | supersede | strong |
| decision(定案) → 同对象 change(落实) | supersede / cause | strong |
| 变更含 before→after 差分且对象明确 | supersede | strong |
| 其余同主题推进 | evolve / trigger | weak |
"""


def main():
    text = io.open(V2, encoding="utf-8").read()
    # 1) 替换 strength 标准段(从标题到 type 含义 前)
    start = text.index("# strength 分级标准(核心新增)")
    end = text.index("# type 含义")
    text = text[:start] + NEW_STRENGTH + "\n" + text[end:]
    # 2) 在 type 含义列表末尾(trigger 行)后插入判型映射
    anchor = "- trigger:前一事件引发后一事件(有先后递进,但不算严格因果)\n"
    assert anchor in text
    text = text.replace(anchor, anchor + TYPE_MAP, 1)
    io.open(OUT, "w", encoding="utf-8").write(text)
    # 校验
    assert "v2.1 扩展" in text and "判型映射补充(v2.1)" in text
    print(f"[ok] {OUT} 生成({len(text)} 字符)")


if __name__ == "__main__":
    raise SystemExit(main())
