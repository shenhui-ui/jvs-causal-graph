#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""M1-02 wrapper:memory_search 原始输出 → 去重 → 事件候选记录(半自动)。

流水线:
  1) 生成原始输出(PowerShell 或 CMD,每关键词一次):
       openclaw.cmd memory search "支付超时"  > raw_search_1.txt
       openclaw.cmd memory search "验收规则"  > raw_search_2.txt
     (关键词清单可取自题集/金标准,一行一词,存 queries.txt)
  2) 处理:
       python m1_02_process.py --raw raw_search_1.txt --out records_1.jsonl
       (或 --raw-dir ./raws 目录批量;输出 records-all.jsonl)

记录格式(每行):
  {query_hint, score, src, hash, content(preview 200), n_dups_dropped, or_rank}

字段定义:score=检索得分;src/hash 来自 [src: path#hash];content=锚点后方 120 字预览;
n_dups_dropped=该行所属指纹被本次去重挤掉的重复行数。

自测:
  python m1_02_process.py --selftest
"""
import argparse
import hashlib
import io
import json
import os
import re

SRC_RE = re.compile(r"\[src:\s*([^#\]]+)(?:#(\S+))?\]")
SCORE_RE = re.compile(r"^\s*([\d.]+)\s+")


def norm_content(line):
    content = SRC_RE.sub("", line)
    content = re.sub(r"^[\d.]+\s+\S+\s*", "", content)
    return content.strip()


def fingerprint(line):
    return hashlib.sha256(norm_content(line).encode("utf-8")).hexdigest()[:16]


def parse_lines(lines):
    rows = []
    for line in lines:
        line = line.rstrip("\n").strip()
        if not line:
            continue
        m = SRC_RE.search(line)
        if not m:
            continue
        src, h = m.group(1).strip(), (m.group(2) or "").strip()
        score_s = SCORE_RE.match(line)
        preview = SRC_RE.sub("", line)
        preview = re.sub(r"^[\d.]+\s+\S+\s*", "", preview).strip()[:120]
        rows.append({"src": src, "hash": h, "score": float(score_s.group(1)) if score_s else None,
                     "preview": preview, "fp": fingerprint(line)})
    return rows


def dedupe_and_emit(rows, query_hint=""):
    seen, n_dup = {}, 0
    out, dropped, order = [], 0, set()
    for i, r in enumerate(rows):
        if r["fp"] in seen:
            dropped += 1
        else:
            seen[r["fp"]] = i
            order.add(i)
    for i in order:
        r = rows[i]
        r["query_hint"] = query_hint
        r["n_dups_dropped"] = dropped if False else 0
        out.append(r)
    # 为每个保留行回填其挤掉的重复数
    counts = {}
    for r in rows:
        counts[r["fp"]] = counts.get(r["fp"], 0) + 1
    for r in out:
        r["n_dups_dropped"] = counts[r["fp"]] - 1
    return out, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="")
    ap.add_argument("--raw-dir", default="")
    ap.add_argument("--out", default="records-all.jsonl")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        lines = []
        for i in range(3):
            c = f"内容{i} 支付超时重试方案变更 [src: workspace/MEMORY.md #abc{i:04d}]"
            lines.append(f"0.72 sessions/main/x.jsonl:{i}-{i} 试策略 - {c}")
            lines.append(f"0.72 sessions/main/x.jsonl:{i + 20}-{i + 20} 试策略 - {c}")
        lines.append("0.60 sessions/main/y.jsonl:9 验收规则按包全验 [src: workspace/memory/2026-08-29.md #def0001]")
        rows = parse_lines(lines)
        out, dropped = dedupe_and_emit(rows, "selftest")
        print(f"selftest: 输入 {len(rows)} 有效行;去重后 {len(out)};丢重复 {dropped}")
        assert dropped == 3 and len(out) == 4, "selftest failed"
        print("selftest PASS")
        return 0

    files = []
    if args.raw:
        files.append(args.raw)
    if args.raw_dir:
        files.extend(os.path.join(args.raw_dir, f) for f in sorted(os.listdir(args.raw_dir))
                     if f.endswith((".txt", ".log")))
    all_rows, total_dropped = [], 0
    for f in files:
        with io.open(f, encoding="utf-8", errors="replace") as fh:
            rows = parse_lines(fh.readlines())
        out, dropped = dedupe_and_emit(rows, os.path.basename(f))
        all_rows.extend(out)
        total_dropped += dropped
    with io.open(args.out, "w", encoding="utf-8") as f:
        for r in all_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[ok] {len(files)} 文件 → {len(all_rows)} 条记录(丢重复 {total_dropped}) → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
