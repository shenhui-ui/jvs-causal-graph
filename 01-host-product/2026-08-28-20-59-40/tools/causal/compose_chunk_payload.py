#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""为单个 chunk 组装抽取 payload(语料组A 分批量用)。"""
import argparse
import hashlib
import io
import re


def prompt_body(prompt_path):
    content = io.open(prompt_path, encoding="utf-8").read()
    idx = content.find("# 二、提示词正文")
    rest = content[idx:]
    code = re.search(r"```(.*?)```", rest, re.S)
    return code.group(1).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    text = io.open(args.chunk, encoding="utf-8").read().strip()
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    body = prompt_body(args.prompt)
    payload = body.replace("{text}", text).replace("{text_hash}", h)
    payload += ("\n\n# 附加说明(调用方追加)\n"
                f"本段来源:语料组A 研发群脱敏时间线,source_hash={h};"
                "每个事件的 source_hash 填本段哈希 h(即 {h} 值);"
                "ts 优先使用消息行开头方括号内的日期(格式 yyyy-mm-dd);原文无日期才填 unknown。"
                "只输出 JSON 数组。\n".replace("{h}", h))
    io.open(args.out, "w", encoding="utf-8").write(payload)
    print(f"[ok] {args.out} ({len(payload)} 字符, hash={h})")


if __name__ == "__main__":
    raise SystemExit(main())
