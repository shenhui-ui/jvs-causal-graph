#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""S4 候选对 → 判定 payload(分批)。

用法:
  python compose_pairs_payload.py --pairs <candidate-pairs.jsonl> --events <all-real-events.jsonl> \
      --prompt <prompt_causal_edges.md> --out <payload.txt> --from 0 --to 60
"""
import argparse
import hashlib
import io
import json
import re


def prompt_body(prompt_path):
    content = io.open(prompt_path, encoding="utf-8").read()
    idx = content.find("# 二、提示词正文")
    rest = content[idx:]
    code = re.search(r"```(.*?)```", rest, re.S)
    return code.group(1).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--events", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--from", dest="start", type=int, default=0)
    ap.add_argument("--to", type=int, default=60)
    args = ap.parse_args()

    evs = {}
    with io.open(args.events, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                e = json.loads(line)
                evs[e["event_id"]] = e

    pair_objs = []
    with io.open(args.pairs, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pair_objs.append(json.loads(line))
    batch = pair_objs[args.start:args.to]

    def summarize(eid):
        e = evs.get(eid, {})
        core = (f"{e.get('event_id')}|{e.get('ts')}|{e.get('type')}|{e.get('subject')} "
                f"{e.get('action')} {e.get('object')}")
        before, after = e.get("before"), e.get("after")
        # 仅 change/bugfix 事件显示 before→after 差分;其余类型直接附 after 作为描述
        if e.get("type") in ("change", "bugfix") and before is not None and after:
            return f"{core} | 变更:由「{before}」→「{after}」"
        if after:
            return f"{core} | {after}"
        return core

    objs = []
    for i, p in enumerate(batch, 1):
        objs.append({
            "pair_no": i,
            "from_event": summarize(p["from_event"]),
            "to_event": summarize(p["to_event"]),
        })
    text = json.dumps(objs, ensure_ascii=False, indent=1)
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    body = prompt_body(args.prompt)
    payload = (body.replace("{events}", text).replace("{text_hash}", h))
    payload += (
        "\n\n# 本批格式说明(调用方追加,优先级最高)\n"
        "输入为候选对数组[ {pair_no, from_event, to_event} ],只对给定候选对判定,不要新增配对、不要修改事件;"
        "输出边数组,每个元素除原字段外加 pair_no(给定对编号);from_event/to_event 填原始事件 id(| 前的部分);"
        "source_hash 填本批 hash(见 {h});铁律不变:无理由不判边。只输出 JSON 数组。\n".replace("{h}", h)
    )
    with io.open(args.out, "w", encoding="utf-8") as f:
        f.write(payload)
    print(f"[ok] {args.out} ({len(payload)} 字符, {len(batch)} 对, hash={h})")


if __name__ == "__main__":
    raise SystemExit(main())
