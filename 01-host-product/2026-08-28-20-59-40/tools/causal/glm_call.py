#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""GLM 判边调用器:复用本机 WorkBuddy 的 GLM 配置(models.json),带 429 退避重试。

用法:
  python glm_call.py --payload <payload.txt> --out <reply.txt>
     读取 payload 全文作为 user 消息发送给 GLM-5.3,回复写入 reply(原文保存,供 extract_array 解析)。
  python glm_call.py --probe      仅连通性测试(ping)。

说明:
- 配置来源:C:\Users\<user>\.workbuddy-ai\models.json(用户授权使用本机 GLM 通道);
- 429(限流)退避:2/4/8/16/30s 递增,最多 5 次;失败抛出并保留原因;
- 只读配置不写回,不打印 apiKey;输出 token 用量到 stderr 供成本核算。
"""
import argparse
import io
import json
import sys
import time
import urllib.request
import urllib.error

CFG_PATH = r"C:\Users\<user>\.workbuddy-ai\models.json"
ENDPOINT_SUFFIX = "/chat/completions"
MODEL = "glm-5.3"


def load_glm():
    arr = json.load(io.open(CFG_PATH, encoding="utf-8"))
    for m in arr:
        if (m.get("id") or "").lower().startswith("glm"):
            return m
    return arr[0]


def chat(prompt, timeout=300):
    cfg = load_glm()
    base = cfg["url"].rstrip("/")
    ep = base + ENDPOINT_SUFFIX
    body = {
        "model": cfg.get("id", MODEL),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 131072,
    }
    delays = [2, 4, 8, 16, 30]
    last = None
    for attempt, d in enumerate(delays + [0]):
        if attempt:
            time.sleep(d)
        try:
            req = urllib.request.Request(
                ep, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + cfg["apiKey"]})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.load(r)
            usage = resp.get("usage") or {}
            print(f"[glm] usage: {usage}", file=sys.stderr)
            # 兼容 reasoning:取 content;若空取 reasoning_content 拼接
            msg = (resp.get("choices") or [{}])[0].get("message") or {}
            content = msg.get("content") or msg.get("reasoning_content") or ""
            return content
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                print(f"[glm] 429 限流,退避 {d}s (第{attempt+1}次)", file=sys.stderr)
                continue
            raise
        except Exception as e:
            last = e
            raise
    raise RuntimeError(f"GLM 429 持续限流,重试耗尽: {last}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()
    if args.probe:
        t = chat("回复 OK")
        print("PROBE OK:", t[:40])
        return 0
    if not args.payload:
        ap.error("需要 --payload 或 --probe")
    text = io.open(args.payload, encoding="utf-8").read()
    reply = chat(text)
    if args.out:
        io.open(args.out, "w", encoding="utf-8").write(reply)
        print(f"[ok] → {args.out} ({len(reply)} 字符)")
    else:
        print(reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
