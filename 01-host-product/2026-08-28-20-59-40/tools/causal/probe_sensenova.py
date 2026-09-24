#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""探测 token.sensenova.cn:列模型 + 试 chat(不打印 key)。"""
import json
import os
import urllib.request
import urllib.error

KEY = os.environ.get("PROBE_KEY", "")
BASE = "https://token.sensenova.cn/v1"


def call(path, method="GET", body=None, timeout=25):
    headers = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def main():
    s, body = call("/models")
    print("GET /models ->", s)
    print("  body:", body[:400])
    if s == 200:
        try:
            ids = [m.get("id") for m in json.loads(body).get("data", [])]
            print("  模型列表:", ids[:40])
        except Exception:
            pass
    # 试 chat
    for model in ["nova-4-plus", "nova-pro", "SenseNova", "sensenova-pro"]:
        s, body = call("/chat/completions", "POST",
                       {"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5})
        print(f"POST chat {model} -> {s}: {body[:120]}")
        if s == 200:
            print("  CHAT OK")
            raise SystemExit(0)
    print("chat 未验证通过")
    raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
