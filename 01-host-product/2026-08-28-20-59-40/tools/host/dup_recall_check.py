#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""M1-02 备件:宿主检索结果重复行召回/误杀检查器(harness + 自测)。

背景:openclaw memory search 返回行格式 —— "得分 文件:行号\n内容摘要... [src: path#hash]";
同源同内容的行会重复出现(如会话 jsonl 32/34/36/38 行同内容)。

用法:
  python dup_recall_check.py selftest                     # 自测(构造 10 对重复 + 10 条非重复)
  python dup_recall_check.py dedupe <rows.txt> --out <dedupe.txt>
      # rows.txt 每行一条检索命中(或整段原样),输出去重结果与统计
"""
import argparse
import hashlib
import io
import re
import sys

SRC_RE = re.compile(r"\[src:\s*([^#\]]+)(?:#(\S+))?\]")


def norm_line(line: str) -> str:
    # 提取 src + 内容指纹(去空白、去行号锚点差异:得分前缀与 路径:行号 前缀)
    m = SRC_RE.search(line)
    src = m.group(1).strip() if m else ""
    content = SRC_RE.sub("", line)
    content = re.sub(r"^[\d.]+\s+\S+\s*", "", content)   # 得分前缀 + 单个锚点 token(路径:行号)
    return content


def line_key(line: str):
    return hashlib.sha256(norm_line(line).encode("utf-8")).hexdigest()[:16]


def dedupe(lines):
    seen, out, dropped = set(), [], []
    for i, line in enumerate(lines):
        k = line_key(line)
        if k in seen:
            dropped.append((i, line))
            continue
        seen.add(k)
        out.append(line)
    return out, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["selftest", "dedupe"])
    ap.add_argument("input", nargs="?", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.cmd == "selftest":
        # 构造:10 对重复(same src+content,不同行号/得分)+ 10 条非重复
        lines = []
        for i in range(10):
            c = f"内容样例{i} 支付超时重试方案变更 [src: workspace/MEMORY.md #abc{i:04d}]\n"
            lines.append(f"0.72 file:{i}:1 {c}")
            lines.append(f"0.72 file:{i}:2 {c}")  # 假重复
        for i in range(10):
            c = f"非重复{i} 验收规则 [src: sessions/main/{i}.jsonl #ddd{i:04d}]"
            lines.append(f"0.60 file:{i}:9 {c}")
        out, dropped = dedupe(lines)
        n_dup = len(dropped)
        recall = n_dup / 10.0 if n_dup else 0.0
        false_kill = max(0, len(lines) - 10 - len(out) + 10 - n_dup)  # 第一遍:out 保留了所有唯一
        kept_nondup = sum(1 for l in out if "非重复" in l)
        print(f"selftest: 输入 {len(lines)} 行;去重后 {len(out)};丢弃 {n_dup}")
        print(f"  重复行召回: {n_dup}/10 = {recall:.0%}(目标 100%)")
        print(f"  非重复误杀: {10 - kept_nondup}/10 = {10 - kept_nondup}(目标 0)")
        return 0 if (n_dup == 10 and kept_nondup == 10) else 1

    # dedupe
    with io.open(args.input, encoding="utf-8", errors="replace") as f:
        lines = [l.rstrip("\n") for l in f]
    out, dropped = dedupe(lines)
    if args.out:
        with io.open(args.out, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
    print(f"dedupe: {len(lines)} → {len(out)};丢弃 {len(dropped)} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
