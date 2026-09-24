#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""找能返回完整判边 JSON 的模型:测试 短prompt+max 与 长prompt+大max。"""
import io
import json
import os
import time
import urllib.request
import urllib.error

KEY = os.environ.get("PROBE_KEY", "")
BASE = "https://token.sensenova.cn/v1"
PAYLOAD = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump\v21b_payload_0.txt"


def chat(model, prompt, max_tokens, extra=None, timeout=150):
    body = {"model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2, "max_tokens": max_tokens}
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
        return e.code, e.read().decode("utf-8", "replace")[:120]
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def content_of(resp):
    try:
        return (resp["choices"][0]["message"].get("content") or "").strip()
    except Exception:
        return ""


def main():
    full = io.open(PAYLOAD, encoding="utf-8").read()
    # 短 prompt:判 2 对,看模型是否直接给 JSON(非 reasoning 优先)
    short = ('请判断以下两对事件之间是否存在因果/关联边,只输出 JSON 数组,不要任何解释:\n'
             '[{"pair_no":1,"from_event":"A1|decision|支付超时重试方案|变更:由「失败重试3次」→「主动查单补偿」",'
             '"to_event":"B1|change|支付超时重试方案|变更:由「失败重试3次」→「主动查单补偿」"},'
             '{"pair_no":2,"from_event":"C1|meeting|评测集|继续标注","to_event":"D1|meeting|评测集|推进"}]')
    cases = [
        ("kimi-k3", short, 2000),
        ("sensenova-u1.5-lite", short, 2000),
        ("glm-5.2", short, 4096, {"reasoning_effort": "low"}),
        ("glm-5.2", full, 32768),
        ("kimi-k3", full, 32768),
    ]
    for c in cases:
        model, prompt, mt = c[0], c[1], c[2]
        extra = c[3] if len(c) > 3 else None
        s, resp = chat(model, prompt, mt, extra)
        if s == 200:
            content = content_of(resp)
            print(f"{model} | {len(prompt)}B | max={mt} | extra={extra} -> 200 | content {len(content)}B | "
                  f"以[开头: {content.startswith('[')}")
            if content.startswith("["):
                print(f"   ok JSON 开头:{content[:60]}")
                return 0
        else:
            print(f"{model} | {len(prompt)}B | max={mt} | extra={extra} -> {s} {str(resp)[:80]}")
        time.sleep(1)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
