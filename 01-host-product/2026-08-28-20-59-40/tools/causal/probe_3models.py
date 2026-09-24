#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""探测 sensenova-6.8-flash-lite / u1.5-lite / u1-fast 可用性与正确参数(短判边测试)。"""
import io
import json
import os
import time
import urllib.request
import urllib.error

KEY = os.environ.get("PROBE_KEY", "")
BASE = "https://token.sensenova.cn/v1"

SHORT = ('请判断以下两对事件之间是否存在因果/关联边,只输出 JSON 数组,不要任何解释:\n'
         '[{"pair_no":1,"from_event":"A1|decision|支付超时重试方案|变更:由「失败重试3次」→「主动查单补偿」",'
         '"to_event":"B1|change|支付超时重试方案|变更:由「失败重试3次」→「主动查单补偿」"},'
         '{"pair_no":2,"from_event":"C1|meeting|评测集|继续标注","to_event":"D1|meeting|评测集|推进"}]')


def chat(model, prompt, temperature, max_tokens, extra=None, timeout=120):
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
            resp = json.load(r)
            msg = (resp["choices"][0]["message"])
            content = (msg.get("content") or "").strip()
            rc = (msg.get("reasoning") or msg.get("reasoning_content") or "")
            return 200, content, rc, resp.get("usage", {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:150], "", {}
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}", "", {}


def main():
    cases = [
        ("sensenova-6.8-flash-lite", 1.0, 4096, None),
        ("sensenova-6.8-flash-lite", 1.0, 16384, None),
        ("sensenova-u1.5-lite", 1.0, 4096, None),
        ("sensenova-u1-fast", 1.0, 4096, None),
        ("sensenova-u1-fast", 1.0, 16384, None),
    ]
    for model, temp, mt, extra in cases:
        s, content, rc, usage = chat(model, SHORT, temp, mt, extra)
        head = content[:50].replace("\n", " ")
        print(f"{model} | max={mt} -> {s} | content {len(content)}B | 以[开头={content.startswith('[')} | {head}")
        if s == 200:
            print(f"   usage: {usage}")
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
