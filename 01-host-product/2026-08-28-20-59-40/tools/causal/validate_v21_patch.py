#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""单样例验证:v2.1 + before 差分能否把「由X改为Y」判成 supersede=strong(不调外部 LLM,只本地验证提示词含关键判据)。"""
import io
import re

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\prompts"


def main():
    for name in ("prompt_causal_edges_v2.1.md",):
        text = io.open(BASE + "\\" + name, encoding="utf-8").read()
        checks = {
            "变更句式判据": "把 A 改为 B" in text or "由 X 改为 Y" in text,
            "决策→变更衔接": "决策→变更衔接" in text,
            "变更差分判据": "before→after 差分" in text,
            "supersede→strong": "supersede" in text and "strong" in text,
            "weak 不参与多跳": "不参与多跳" in text,
        }
        print(f"== {name} ==")
        for k, v in checks.items():
            print(f"  {'✓' if v else '✗'} {k}")
        ok = all(checks.values())
        print("判定:补丁判据完整 =", "PASS" if ok else "FAIL(补丁不完整,重判前需修复)")


if __name__ == "__main__":
    raise SystemExit(main())
