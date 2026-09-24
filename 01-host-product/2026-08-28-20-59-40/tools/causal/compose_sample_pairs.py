#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""从 sample-pairs-60.jsonl 组装 v2 试判 payload(预期标签不注入提示词)。

用法:
  python compose_sample_pairs.py --pairs sample-pairs-60.jsonl --prompt prompt_causal_edges_v2.md --out payload_v2_60.txt
"""
import argparse
import hashlib
import io
import json
import re


def prompt_body(path):
    content = io.open(path, encoding="utf-8").read()
    idx = content.find("# 二、提示词正文")
    rest = content[idx:]
    code = re.search(r"```(.*?)```", rest, re.S)
    return code.group(1).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    objs = []
    with io.open(args.pairs, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            p = json.loads(line)
            objs.append({
                "pair_no": int(p.get("pair_no", len(objs) + 1)),
                "from_event": p.get("from_text") or f"{p.get('from_ts')} {p.get('from_subj')}",
                "to_event": p.get("to_text") or f"{p.get('to_ts')} {p.get('to_subj')}",
            })
    text = json.dumps(objs, ensure_ascii=False, indent=1)
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    body = prompt_body(args.prompt)
    payload = body.replace("{events}", text).replace("{text_hash}", h)
    payload += (
        "\n\n# 本批格式说明(调用方追加,优先级最高)\n"
        "输入为候选对数组 [{pair_no, from_event, to_event}](字符串 summarize,非事件对象);"
        "只对给定候选对判定;输出边数组:原字段(edge_id/from_event/to_event/type/strength/rationale/evidence/confidence/source_hash)"
        "外加 pair_no(对应该输入的 pair_no);from_event/to_event 填对应事件 id(字符串最前的事件号,若无可填 pair_no 并注明)。"
        "strength 必填(strong/weak);无依据不判边。只输出 JSON 数组。\n"
    )
    with io.open(args.out, "w", encoding="utf-8") as f:
        f.write(payload)
    print(f"[ok] {args.out} ({len(payload)} 字符, {len(objs)} 对, hash={h})")


if __name__ == "__main__":
    raise SystemExit(main())
