#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""深挖两个 SenseNova 模型:原始响应结构 + 精确模型 ID 列表。"""
import io
import json
import os
import urllib.request
import urllib.error

KEY = os.environ.get("PROBE_KEY", "")
BASE = "https://token.sensenova.cn/v1"
SHORT = '判断以下两对事件:只输出 JSON 数组 [{"pair_no":1,"from_event":"A1|支付超时方案|由「失败重试3次」→「主动查单补偿」","to_event":"B1|支付超时方案|由「失败重试3次」→「主动查单补偿」"},{"pair_no":2,"from_event":"C1|评测集|标注","to_event":"D1|评测集|推进"}]'


def raw_get(path):
    req = urllib.request.Request(BASE + path, headers={"Authorization": "Bearer " + KEY})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]


def raw_chat(model, body_extra=None, max_tokens=500):
    body = {"model": model, "messages": [{"role": "user", "content": SHORT}],
            "temperature": 1.0, "max_tokens": max_tokens}
    if body_extra:
        body.update(body_extra)
    req = urllib.request.Request(BASE + "/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + KEY})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]


def main():
    s, body = raw_get("/models")
    data = json.loads(body).get("data", [])
    print(f"== /models ({len(data)} 个) ==")
    for m in data:
        print("   id =", m.get("id"))
    print()
    for model in ["sensenova-6.8-flash-lite", "sensenova-u1.5-lite"]:
        s, body = raw_chat(model)
        print(f"== chat {model} -> {s} ==")
        print("   raw:", body[:500].replace("\n", " "))
        print()


if __name__ == "__main__":
    raise SystemExit(main())
