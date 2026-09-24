#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""验证 token.sensenova.cn 的 chat 通路(用真实模型 ID)。"""
import json
import os
import urllib.request
import urllib.error

KEY = os.environ.get("PROBE_KEY", "")
BASE = "https://token.sensenova.cn/v1"


def chat(model, prompt, max_tokens=8):
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(BASE + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + KEY})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def main():
    for m in ["deepseek-v4-flash", "glm-5.2", "sensenova-6.7-flash-lite", "kimi-k3"]:
        s, body = chat(m, "回复两个字:明白")
        ok = s == 200 and "明白" in body
        print(f"chat {m} -> {s} | {('OK' if ok else body[:100])}")
        if s == 200:
            # 打印内容
            try:
                c = json.loads(body)["choices"][0]["message"]["content"]
                print("   内容:", c)
            except Exception:
                pass
            return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
