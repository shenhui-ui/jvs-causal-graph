#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe one explicitly selected provider using its dedicated environment key."""
import argparse
import json
import os
import urllib.error
import urllib.request

TARGETS = {
    "siliconflow": ("SILICONFLOW_API_KEY", "https://api.siliconflow.cn/v1/chat/completions", ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-7B-Instruct"]),
    "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1/chat/completions", ["llama-3.3-70b-versatile"]),
    "cerebras": ("CEREBRAS_API_KEY", "https://api.cerebras.ai/v1/chat/completions", ["llama-3.3-70b"]),
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/chat/completions", ["deepseek/deepseek-chat"]),
    "mistral": ("MISTRAL_API_KEY", "https://api.mistral.ai/v1/chat/completions", ["mistral-small-latest"]),
    "together": ("TOGETHER_API_KEY", "https://api.together.xyz/v1/chat/completions", ["meta-llama/Llama-3.3-70B-Instruct-Turbo"]),
    "deepinfra": ("DEEPINFRA_API_KEY", "https://api.deepinfra.com/v1/openai/chat/completions", ["meta-llama/Llama-3.3-70B-Instruct"]),
    "xai": ("XAI_API_KEY", "https://api.x.ai/v1/chat/completions", ["grok-2-latest"]),
    "volcengine": ("VOLCENGINE_API_KEY", "https://ark.cn-beijing.volces.com/api/v3/chat/completions", ["doubao-lite-32k"]),
    "minimax": ("MINIMAX_API_KEY", "https://api.minimaxi.com/v1/text/chatcompletion_v2", ["abab6.5s-chat"]),
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
            with urllib.request.urlopen(req, timeout=20) as response:
                print(f"[OK] {args.provider} / {model} -> {response.status}")
                return 0
        except urllib.error.HTTPError as exc:
            print(f"[{exc.code}] {args.provider} / {model}")
        except Exception as exc:
            print(f"[ERR] {args.provider} / {model}: {type(exc).__name__}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
