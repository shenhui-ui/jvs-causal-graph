#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""S7 事件抽取召回率评分器(v2,稳定匹配)。

用法:
  python score_recall.py --raw <s3_v1_raw_reply.txt> --gold <gold-events.jsonl> \
      --extra-gold <gold-extra.jsonl> --out <score.md> [--ignore-type]

v2 改进:
- 每个金标准先计算候选抽取项(ts 相同 + 主体/对象关键词命中);
- 按候选数升序处理,取未使用的候选(确定性贪心,避免先到先得抢注);
- 候选全被占用但与其它金标准共享同一候选 → 记「粒度合并」而非漏抽;
- 未命中金标准的抽取项进入「过提取/编造人工复核」清单。

指标口径:
- 事实槽 = 金标准 − 合并数;召回率 = 命中 / 事实槽;
- 精确率 = 命中 / 抽取总数;过提取 = 抽取总数 − 命中(需人工判定是否语料真实);
- 编造数 = 过提取中「语料无依据」的条数(人工判定后回填)。
"""

import argparse
import json
import re


def extract_array(path):
    """从可能含警告/日志前缀的文件中提取第一个 JSON 数组(括号配对,不受文中 ] 干扰)。"""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    start = raw.find("[")
    # 跳过前缀里的 [ 开头的警告(如 [plugins]):只接受后面跟 { 或换行白的真实数组
    i = 0
    for m in re.finditer(r"\[", raw):
        seg = raw[m.start(): m.start() + 2]
        if seg in ("[{", "[\n", "[ "):
            start = m.start()
            break
    depth = 0
    for idx in range(start, len(raw)):
        ch = raw[idx]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return json.loads(raw[start: idx + 1])
    return None


def norm(s):
    return re.sub(r"[\s（）()【】\[\],。·、:：\-—/\\]", "", s or "")


def _ngrams(s, n=2):
    s = norm(s)
    return {s[i:i + n] for i in range(len(s) - n + 1)} if len(s) >= n else {s}


def _sim(a, b):
    A, B = _ngrams(a), _ngrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / max(1, min(len(A), len(B)))


def candidates(g, exts, ignore_type):
    gs, go = norm(g.get("subject")), norm(g.get("object"))
    ga = norm(g.get("after") or "")
    c = []
    for idx, e in enumerate(exts):
        if e.get("ts") != g.get("ts"):
            continue
        if not ignore_type and e.get("type") != g.get("type"):
            continue
        es, eo = norm(e.get("subject")), norm(e.get("object"))
        ea = norm(e.get("after") or "")
        hit_subj = any(a and b and (a in b or b in a) and min(len(a), len(b)) >= 2
                       for a, b in [(gs, es), (gs, eo), (go, es), (go, eo)])
        hit_after = ga and ea and _sim(ga, ea) >= 0.35
        if hit_subj or hit_after:
            c.append(idx)
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--extra-gold", default="")
    ap.add_argument("--ignore-type", action="store_true")
    args = ap.parse_args()

    exts = extract_array(args.raw) or []
    gold = []
    for path in [args.gold, args.extra_gold] if args.extra_gold else [args.gold]:
        if not path:
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    gold.append(json.loads(line))

    plan = []
    for gi, g in enumerate(gold):
        c = candidates(g, exts, args.ignore_type)
        plan.append((len(c), gi, c))

    used, hits, merged, missed = set(), [], [], []
    for _, gi, c in sorted(plan, key=lambda x: (x[0], x[1])):
        g = gold[gi]
        free = [i for i in c if i not in used]
        if free:
            used.add(free[0])
            hits.append((g, free[0]))
        elif c and any(i in used for i in c):
            merged.append(g)
        else:
            missed.append(g)

    total = len(exts)
    fact_slots = len(gold) - len(merged)
    prec = len(hits) / total if total else 0.0
    rec = len(hits) / fact_slots if fact_slots else 0.0
    over = total - len(hits)

    lines = [f"# S3 抽取召回率评分(v2)\n",
             f"- 金标准:{len(gold)};其中粒度合并:{len(merged)};事实槽:{fact_slots};抽取:{total};命中:{len(hits)};"
             f"漏抽:{len(missed)};过提取(待人工判编造):{over}",
             f"- **召回率(按事实槽)={rec:.1%}  精确率={prec:.1%}**\n",
             "## 粒度合并(一个抽取项承载两个金标准事实)\n"]
    for g in merged:
        lines.append(f"- [{g.get('ts')}] {g.get('type')} {g.get('subject')} {g.get('action')} {g.get('object')}")
    lines.append("\n## 漏抽(真漏)\n")
    for g in missed:
        lines.append(f"- [{g.get('ts')}] {g.get('type')} {g.get('subject')} {g.get('action')} {g.get('object')}")
    lines.append("\n## 过提取清单(人工判定:语料真实→过提取;语料无依据→编造)\n")
    for idx, e in enumerate(exts):
        if idx in used:
            continue
        lines.append(f"- [{e.get('ts')}] {e.get('type')} {e.get('subject')} {e.get('action')} {e.get('object')} "
                     f"(before={e.get('before')}, after={e.get('after')})")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines[:4]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
