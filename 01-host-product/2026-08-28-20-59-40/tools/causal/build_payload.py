#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""S3 步骤 1:从 S2 dump 组装「事件抽取」payload(不调用任何模型)。

用法:
  python build_payload.py --dump <memory_dump.jsonl> --prompt <prompt_event_extract.md> --out <payload.txt>

说明:
- 从 dump 中挑选 kinds: daily / long_term / user_profile / session(排除系统文件 other);
- 每段前加 [src: <path> #<hash12>] 标注,供模型回填 source_hash;
- 替换提示词正文 {text} 与 {text_hash};并追加一行「source_hash 按每段 [src] 标注中的哈希填写」。
"""

import argparse
import json
import re

KEEP = {"daily", "long_term", "user_profile", "session"}
BODY_MARK = "# 二、提示词正文"


def prompt_body(prompt_path: str) -> str:
    with open(prompt_path, "r", encoding="utf-8") as f:
        content = f.read()
    idx = content.find(BODY_MARK)
    if idx < 0:
        raise SystemExit("未找到提示词正文标记")
    rest = content[idx + len(BODY_MARK):]
    code = re.search(r"```(.*?)```", rest, re.S)
    if not code:
        raise SystemExit("未找到代码块")
    return code.group(1).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--extra", default=[], action="append",
                    help="额外素材文件(md/txt),作为独立来源加入(可多次)")
    args = ap.parse_args()

    sections = []
    with open(args.dump, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("kind") not in KEEP:
                continue
            h = rec.get("source_hash", "")[:12]
            sections.append(
                f"[src: {rec['source']} #{h}]\n{rec.get('content', '')}"
            )
    # 额外真实/脱敏素材文件(独立来源)
    for path in args.extra or []:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        import hashlib
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
        label = f"REAL:{path.replace('\\\\', '/').split('/')[-1]}"
        sections.append(f"[src: {label} #{h}]\n{content}")
    if not sections:
        raise SystemExit("dump 中没有可抽取的记忆记录")

    text = "\n\n".join(sections)
    body = prompt_body(args.prompt)
    payload = body.replace("{text}", text) + "\n\n"
    payload += ("# 附加说明(调用方追加)\n"
                "每个事件的 source_hash 字段,请填写该事件出处 section 标注中的哈希"
                "(形如 [src: workspace/MEMORY.md #75204990686d] 中的 # 后 12 位),"
                "不要填 {text_hash} 占位。trust_level 按你的判断。"
                "只输出 JSON 数组,不要任何解释。\n")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(payload)
    print(f"[ok] payload 已生成: {args.out} ({len(payload)} 字符, {len(sections)} 个来源)")
    for s in sections:
        print(f"  - {s.splitlines()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
