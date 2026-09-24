#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""SenseNova 6.8-flash-lite 与 u1.5-lite 完整 payload 测试(大 max_tokens + 解析 content/reasoning)。"""
import io
import json
import os
import urllib.request
import urllib.error

KEY = os.environ.get("PROBE_KEY", "")
BASE = "https://token.sensenova.cn/v1"
PAYLOAD = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump\v21b_payload_0.txt"


def raw_chat(model, prompt, temperature, max_tokens, extra=None, timeout=180):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature, "max_tokens": max_tokens}
    if extra:
        body.update(extra)
    req = urllib.request.Request(BASE + "/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + KEY})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def main():
    full = io.open(PAYLOAD, encoding="utf-8").read()
    cases = [
        ("sensenova-6.8-flash-lite", full, 1.0, 32768, None),
        ("sensenova-u1.5-lite", full, 1.0, 16384, None),
    ]
    for model, prompt, temp, mt, extra in cases:
        s, resp = raw_chat(model, prompt, temp, mt, extra)
        if s != 200:
            print(f"{model} -> {s} {str(resp)[:120]}")
            continue
        msg = resp["choices"][0]["message"]
        content = (msg.get("content") or "").strip()
        reasoning = (msg.get("reasoning") or msg.get("reasoning_content") or "").strip()
        usage = resp.get("usage", {})
        print(f"== {model} == 200 | content {len(content)}B | reasoning {len(reasoning)}B | usage {usage}")
        print(f"   content 开头: {content[:80].replace(chr(10),' ')}")
        if not content and reasoning:
            print(f"   reasoning 开头: {reasoning[:80].replace(chr(10),' ')}")
            print("   → 只有 reasoning,无 content(可能 max_tokens 仍不够/模型把答案放 reasoning)")


if __name__ == "__main__":
    raise SystemExit(main())
