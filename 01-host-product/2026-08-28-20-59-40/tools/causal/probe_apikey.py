#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe one explicitly selected provider using its dedicated environment key."""
import argparse
import json
import os
import urllib.error
import urllib.request

TARGETS = {
    "deepseek": ("DEEPSEEK_API_KEY", "https://api.deepseek.com/chat/completions", ["deepseek-chat", "deepseek-v4-flash"]),
    "zhipu-glm": ("ZHIPU_API_KEY", "https://open.bigmodel.cn/api/paas/v4/chat/completions", ["glm-4-flash", "glm-5.3"]),
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1/chat/completions", ["gpt-4o-mini"]),
    "moonshot": ("MOONSHOT_API_KEY", "https://api.moonshot.cn/v1/chat/completions", ["moonshot-v1-8k"]),
    "aliyun-qwen": ("DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", ["qwen-plus"]),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True, choices=sorted(TARGETS))
    args = ap.parse_args()
    env_name, endpoint, models = TARGETS[args.provider]
    key = os.environ.get(env_name, "")
    if not key:
        print(f"{env_name} 未设置")
        return 2
    for model in models:
        body = json.dumps({"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}).encode()
        req = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                print(f"[OK] {args.provider} / {model} -> {response.status}")
                return 0
        except urllib.error.HTTPError as exc:
            print(f"[{exc.code}] {args.provider} / {model}")
        except Exception as exc:
            print(f"[ERR] {args.provider} / {model}: {type(exc).__name__}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
