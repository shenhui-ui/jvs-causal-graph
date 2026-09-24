#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""飞书群聊备份 JSON → 脱敏时间线日志(可复用)。

用法:
  python desensitize_feishu.py --input <backup.json> --out <日志.md> [--mapping <映射.md>]

处理:
- 保留 msg_type: text / post / merge_forward / interactive(仅其文本内容,如果 content 是 JSON 则尽量取可读字段);
- 丢弃: system / image / file / media / video_chat / share_calendar_event / folder;
- 发送者 → Z 代号(稳定映射表,自动分配 Z01 起;已有映射可通过 --known 传入);
- 链接/UUID/token 脱敏:https?://... → [链接已脱敏];uuid/uuid=... → [uuid 已脱敏];token=... → [token 已脱敏];
- 保留 create_time,格式 [YYYY-MM-DD HH:mm]。
输出:时间线日志 + 映射表(另写文件)。
"""

import argparse
import json
import re
import io
import os

KEEP_TYPES = {"text", "post", "merge_forward"}
LINK_RE = re.compile(r"https?://\S+")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
TOKEN_RE = re.compile(r"token=[A-Za-z0-9]{6,}")
# v2:正文真名/昵称→代号(昵称映射到真名,再走 code_of)
ALIAS = {"Z-UNMAPPED": "Z-UNMAPPED", "Z18": "Z18", "Z01": "Z01", "Z-ALIAS": "Z01"}


def clean_text(s):
    s = LINK_RE.sub("[链接已脱敏]", s or "")
    s = UUID_RE.sub("[uuid已脱敏]", s)
    s = TOKEN_RE.sub("[token已脱敏]", s)
    s = re.sub(r"<[^>]{2,80}>", "[引用元素]", s)  # 富文本标签
    return s.strip()


def extract_content(m):
    t = m.get("msg_type")
    c = m.get("content") or ""
    if t == "text":
        return c
    if t in ("post", "merge_forward", "interactive"):
        # post/merge 的 content 常为 JSON 或富文本串,取里面的文本字段
        s = c
        if c.lstrip().startswith("{"):
            try:
                obj = json.loads(c)
                parts = []
                for x in obj.get("content", []):
                    if isinstance(x, str):
                        parts.append(x)
                    elif isinstance(x, dict):
                        parts.append(x.get("text") or x.get("title") or "")
                s = " ".join(p for p in parts if p)
            except Exception:
                s = c
        return s
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mapping", default="")
    ap.add_argument("--known", default="", help="形如 Z18=Z18,Z26=Z26 的既有映射")
    ap.add_argument("--extra-names", default="",
                    help="正文替换表补充名单(逗号分隔;覆盖『仅出现在正文,非发送者』的人,如 Z39,Z01,Z-UNMAPPED)")
    args = ap.parse_args()

    known = {}
    for pair in (args.known or "").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            known[k.strip()] = v.strip()

    data = json.load(io.open(args.input, encoding="utf-8"))
    msgs = data.get("data", {}).get("messages", [])
    nxt = 30  # 从 Z30 开始自动分配(已见 Z18/Z26/Z27/Z28 由 known 提供)
    used = set(known.values())
    auto = {}

    def code_of(name):
        nonlocal nxt
        if name in known:
            return known[name]
        if name not in auto:
            while f"Z{nxt}" in used:
                nxt += 1
            auto[name] = f"Z{nxt}"
            used.add(auto[name])
            nxt += 1
        return auto[name]

    # 第一遍:收集所有发言人并稳定编代号
    names = []
    seen = set()
    for m in msgs:
        t = m.get("msg_type")
        if t not in KEEP_TYPES:
            continue
        snd = (m.get("sender") or {}).get("name") or "?"
        if snd in ("飞书提醒 ", "飞书提醒") or snd == "?":
            continue
        if snd not in seen:
            seen.add(snd)
            names.append(snd)
    extra_names = [n.strip() for n in (args.extra_names or "").split(",") if len(n.strip()) >= 2]
    for n in extra_names:
        if n not in seen:
            seen.add(n)
            names.append(n)
    for nm in names:
        code_of(nm)  # 预分配
    mention_names = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    mention_re = re.compile(r"@" + "(" + mention_names + r")")
    # v2:正文(含嵌套引用)中的全名/昵称 → 代号
    body_names = sorted(set(names) | set(ALIAS.keys()), key=len, reverse=True)
    body_pat = re.compile("|".join(re.escape(n) for n in body_names))

    def _code_of_term(t):
        return code_of(ALIAS.get(t, t))

    def desens(body):
        body = clean_text(body)
        body = mention_re.sub(lambda m: "@" + code_of(m.group(1)), body)
        body = body_pat.sub(lambda m: _code_of_term(m.group(0)), body)
        return body

    lines = []
    for m in msgs:
        t = m.get("msg_type")
        if t not in KEEP_TYPES:
            continue
        ts = m.get("create_time", "")
        snd = (m.get("sender") or {}).get("name") or "?"
        if snd in ("飞书提醒 ", "飞书提醒"):
            continue
        body = desens(extract_content(m))
        if not body:
            continue
        lines.append(f"[{ts}] {code_of(snd)}: {body}")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("# 语料组A-finger 研发沟通群(脱敏时间线)[真实素材-已脱敏]\n\n")
        f.write(f"> 来源:飞书备份 {args.input.split(os.sep)[-1]} ｜ 条数:{len(lines)} ｜ 区间:{msgs[0]['create_time']} → {msgs[-1]['create_time']}\n\n")
        f.write("\n".join(lines))

    allmap = {**known, **auto}
    if args.mapping:
        with open(args.mapping, "w", encoding="utf-8") as f:
            f.write(f"# 代号映射(语料组A 群)共 {len(allmap)} 人\n\n")
            for k, v in sorted(allmap.items(), key=lambda x: x[1]):
                f.write(f"- {v} = {k}\n")

    print(f"[ok] {len(lines)} 条 → {args.out}")
    print(f"[ok] 发言人映射 {len(allmap)} 个(新增自动 {len(auto)})")
    print(f"[ok] 示例:")
    for l in lines[:5]:
        print("   ", l[:90])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
