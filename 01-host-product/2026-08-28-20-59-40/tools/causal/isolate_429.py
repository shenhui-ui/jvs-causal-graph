#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""定位 429:隔离模型 / max_tokens / payload 大小。"""
import io
import json
import os
import sys
import time
import urllib.request
import urllib.error

KEY = os.environ.get("PROBE_KEY", "")
BASE = "https://token.sensenova.cn/v1"
PAYLOAD = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump\v21b_payload_0.txt"


def chat(model, prompt, max_tokens, timeout=120):
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0.2, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(BASE + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + KEY})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:100]
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def main():
    full = io.open(PAYLOAD, encoding="utf-8").read()
    small = "回复两个字:明白"
    cases = [
        ("deepseek-v4-flash", small, 8),
        ("deepseek-v4-flash", small, 8192),
        ("deepseek-v4-flash", full[:2000], 8192),
        ("deepseek-v4-flash", full, 256),
        ("deepseek-v4-flash", full, 8192),
        ("sensenova-6.7-flash-lite", full, 8192),
        ("glm-5.2", full, 8192),
    ]
    for model, prompt, mt in cases:
        s, msg = chat(model, prompt, mt)
        print(f"model={model} | payload={len(prompt)} | max_tok={mt} -> {s} {msg}")
        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
