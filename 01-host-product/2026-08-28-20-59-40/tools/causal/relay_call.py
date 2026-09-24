#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""通用 OpenAI 兼容中转调用器(SenseNova token.sensenova.cn 等)。

用法:
  python relay_call.py --payload <p.txt> --out <r.txt> [--base https://token.sensenova.cn/v1] [--model deepseek-v4-flash]
  python relay_call.py --probe
读取:
  - key 从环境变量 RELAY_KEY 或 PROBE_KEY;base/model 用参数(默认 sense nova)。
429 退避:2/4/8/16/30s;reply 保存原文(供 extract_array 解析),支持 reasoning_content。
"""
import argparse
import io
import json
import os
import sys
import time
import urllib.request
import urllib.error

DEFAULT_BASE = "https://token.sensenova.cn/v1"
DEFAULT_MODEL = "glm-5.2"
DEFAULT_REASONING = "low"


def chat(prompt, base=DEFAULT_BASE, model=DEFAULT_MODEL, timeout=240, max_tokens=16384,
         reasoning_effort=DEFAULT_REASONING, temperature=0.2):
    key = os.environ.get("RELAY_KEY") or os.environ.get("PROBE_KEY") or ""
    if not key:
        raise RuntimeError("RELAY_KEY/PROBE_KEY 未设置")
    ep = base.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if reasoning_effort and reasoning_effort not in ("none", "off", "false"):
        body["reasoning_effort"] = reasoning_effort
    delays = [2, 4, 8, 16, 30]
    for attempt, d in enumerate(delays + [0]):
        if attempt:
            time.sleep(d)
        try:
            req = urllib.request.Request(
                ep, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + key})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.load(r)
            usage = resp.get("usage") or {}
            print(f"[relay] usage: {usage}", file=sys.stderr)
            msg = ((resp.get("choices") or [{}])[0].get("message")) or {}
            content = msg.get("content") or msg.get("reasoning_content") or ""
            return content
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"[relay] 429 退避 {d}s", file=sys.stderr)
                continue
            raise
        except Exception:
            raise
    raise RuntimeError("429 持续限流")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=16384)
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--reasoning-effort", default=DEFAULT_REASONING)
    ap.add_argument("--temperature", type=float, default=0.2)
    args = ap.parse_args()
    if args.probe:
        r = chat("回复两个字:明白", args.base, args.model,
                 reasoning_effort=args.reasoning_effort, temperature=args.temperature)
        print("PROBE:", repr(r[:40]))
        return 0
    text = io.open(args.payload, encoding="utf-8").read()
    reply = chat(text, args.base, args.model, timeout=args.timeout,
                 max_tokens=args.max_tokens, reasoning_effort=args.reasoning_effort,
                 temperature=args.temperature)
    io.open(args.out, "w", encoding="utf-8").write(reply)
    print(f"[ok] → {args.out} ({len(reply)} 字符)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
