#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""把脱敏时间线 md 切成 N 段(按日期边界优先,兼顾条数上限)。"""
import argparse
import io
import os
import re


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--lines-per-chunk", type=int, default=200)
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    with io.open(args.input, encoding="utf-8") as f:
        lines = f.read().splitlines()
    # 只取正文行([YYYY-MM-DD ...])
    body = [l for l in lines if l.startswith("[20")]
    chunks = []
    cur = []
    last_date = None
    for l in body:
        m = re.match(r"\[(\d{4}-\d{2}-\d{2})", l)
        date = m.group(1) if m else None
        if len(cur) >= args.lines_per_chunk and date != last_date:
            chunks.append(cur)
            cur = []
        cur.append(l)
        last_date = date
    if cur:
        chunks.append(cur)

    names = []
    for i, c in enumerate(chunks, 1):
        p = os.path.join(args.outdir, f"chunk_{i:02d}.md")
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(f"# 语料组A 群聊抽取第 {i} 段(共 {len(chunks)} 段)[真实素材-已脱敏]\n\n")
            f.write("\n".join(c))
        d0 = re.match(r"\[(\d{4}-\d{2}-\d{2})", c[0]).group(1)
        d1 = re.match(r"\[(\d{4}-\d{2}-\d{2})", c[-1]).group(1)
        print(f"{p}  {len(c)} 条  {d0} → {d1}")
    print(f"总计 {len(chunks)} 段")


if __name__ == "__main__":
    raise SystemExit(main())
