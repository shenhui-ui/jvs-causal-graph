#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""M1 项目状态审计:核对关键资产计数/一致性/残留口径。"""
import io
import json
import os
import re

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"
OK, WARN, FAIL = [], [], []


def cnt(path):
    n = 0
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def load_jsonl(path):
    out = []
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# 1) 事件库
evs = load_jsonl(BASE + r"\all-real-events.jsonl")
ids = [e["event_id"] for e in evs]
OK.append(f"事件库 {len(evs)} 条,唯一 id {len(set(ids))}")

# 2) 题集 v1
qs = load_jsonl(BASE + r"\query-set-real-v1.jsonl")
OK.append(f"题集 v1 {len(qs)} 条")
end_yes = sum(1 for q in qs if q.get("endpoint_reachable") == "yes")
OK.append(f"  endpoint_reachable yes={end_yes}/30")
low = [q["qid"] for q in qs if q.get("low_confidence")]
OK.append(f"  低置信标注={low}(应为 Q019/Q020/Q029/Q030)")
q001 = [q for q in qs if q["qid"] == "Q001"][0]
if "R60162" in json.dumps(q001.get("gold_answer"), ensure_ascii=False):
    WARN.append("Q001 gold_answer 仍含 R60162/163(应仅 note 历史引用)")
else:
    OK.append("  Q001 gold_answer 无 R6016x 残留")

# 3) 金标准
gold = load_jsonl(BASE + r"\gold-events-real-50-init.jsonl")
if any("待人工复核" in (g.get("备注") or "") for g in gold):
    FAIL.append("金标准仍有『待人工复核』残留")
else:
    OK.append(f"金标准 {len(gold)} 条,无待复核残留")

# 4) 身份
with io.open(BASE + r"\incoming_real\identities-v1.json", encoding="utf-8") as f:
    idn = json.load(f)
ids_ = idn.get("identities", [])
h = sum(1 for i in ids_ if i.get("confidence") == "high")
l = sum(1 for i in ids_ if i.get("confidence") == "low")
OK.append(f"身份 v1 {len(ids_)} 条(high {h} / low {l})")

# 5) 候选
cand = load_jsonl(BASE + r"\candidates-m1.jsonl")
evset = set(ids)
bad = [c for c in cand if c["from_event"] not in evset or c["to_event"] not in evset]
if bad:
    FAIL.append(f"候选有 {len(bad)} 对端点不在事件库(如 {bad[:2]})")
else:
    OK.append(f"候选 {len(cand)} 对,端点全部在库")
chan = {}
for c in cand:
    for ch in c.get("channels", []):
        chan[ch] = chan.get(ch, 0) + 1
OK.append(f"  通道覆盖={chan}")

# 6) 边
ed = load_jsonl(BASE + r"\all-real-edges-final.jsonl")
bad_e = [e for e in ed if e["from_event"] not in evset or e["to_event"] not in evset]
if bad_e:
    WARN.append(f"边 {len(ed)} 条,{len(bad_e)} 条端点异常")
else:
    OK.append(f"边 {len(ed)} 条,端点正常")
dup = len(ed) - len({(e["from_event"], e["to_event"]) for e in ed})
if dup:
    WARN.append(f"边重复对 {dup} 组")

# 7) 冒烟判分一致性
sc = json.load(io.open(BASE + r"\smoke-scores.json", encoding="utf-8"))
m = sc.get("metrics", {})
av = m.get("availability", {}).get("available_rate")
ca = m.get("causal_accuracy", {})
ca_v = ca.get("causal_accuracy", ca.get("rate"))
OK.append(f"判分冒烟:可用率={av:.3f},因果={ca_v:.3f}")

# 8) 残留口径扫描
stale = ["366", "13/27", "22.5", "22.7", "点估计<1%（即 0 条）", "14 批", "95% 置信下编造边率 <1%"]
for kw in stale:
    for root, _, fs in os.walk(BASE):
        for fn in fs:
            if fn.endswith((".md", ".json", ".jsonl", ".txt")) and not fn.startswith("~$"):
                p = os.path.join(root, fn)
                try:
                    t = io.open(p, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                if kw in t:
                    WARN.append(f"残留口径『{kw}』→ {os.path.relpath(p, BASE)}")
                    break

print("== 审计结果 ==")
for s in OK:
    print("  [OK]   ", s)
for w in WARN:
    print("  [WARN] ", w)
for f in FAIL:
    print("  [FAIL] ", f)
print(f"\n汇总:OK {len(OK)} / WARN {len(WARN)} / FAIL {len(FAIL)}")
