#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""S4 步骤 1:从 events-v0.jsonl 组装「因果判定」payload(不调用任何模型)。

用法:
  python build_causal_payload.py --events <events-v0.jsonl> --prompt <prompt_causal_edges.md> --out <payload_causal.txt>

说明:
- 因果提示词要求输入事件数组,每事件至少含 event_id/ts/type/subject/original(原始摘要);
- original 由事件字段拼装:日期 + 主体 + 动作 + object + (before→after),并附 [src hash];
- {text_hash} 替换为各事件 source_hash 前缀的拼接(保持一致,保证可回溯)。
"""

import argparse
import json
import re


def prompt_body(prompt_path: str) -> str:
    with open(prompt_path, "r", encoding="utf-8") as f:
        content = f.read()
    mark = "# 二、提示词正文"
    idx = content.find(mark)
    if idx < 0:
        raise SystemExit("未找到提示词正文标记")
    rest = content[idx + len(mark):]
    code = re.search(r"```(.*?)```", rest, re.S)
    if not code:
        raise SystemExit("未找到代码块")
    return code.group(1).strip()


def to_input(ev) -> dict:
    ts = ev.get("ts", "unknown")
    parts = [f"{ts}", ev.get("subject", ""), ev.get("action", ""), ev.get("object", "") or ""]
    if ev.get("before") is not None and ev.get("after") is not None:
        parts.append(f"由「{ev['before']}」变为「{ev['after']}」")
    elif ev.get("before") is not None:
        parts.append(f"为「{ev['before']}」")
    original = " ".join(p for p in parts if p)
    src = ev.get("source_hash", "")[:12]
    return {
        "event_id": ev["event_id"],
        "ts": ts,
        "type": ev.get("type", ""),
        "subject": ev.get("subject", ""),
        "original": f"{original} [src:{src}]",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    evs = []
    hashes = []
    with open(args.events, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            evs.append(to_input(ev))
            hashes.append(ev.get("source_hash", "")[:12])
    if not evs:
        raise SystemExit("无事件可判定")

    body = prompt_body(args.prompt)
    text_hash = hashes[0] if hashes else "NONE"  # 修复:不再拼接多 hash,取首个来源(证据一致性见附加说明)
    payload = (body.replace("{events}", json.dumps(evs, ensure_ascii=False, indent=2))
                  .replace("{text_hash}", text_hash))
    payload += ("\n\n# 附加说明(调用方追加)\n"
                "原事件信息来自 S3 抽取(events jsonl),每事件的 [src:哈希] 为来源;"
                "evidence 请忠实引用 original 中的句子;source_hash 填对应的 [src:] 哈希(勿拼接多个)。"
                "只输出 JSON 数组。\n")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(payload)
    print(f"[ok] causal payload 已生成: {args.out} ({len(payload)} 字符, {len(evs)} 个事件, hash={text_hash})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
