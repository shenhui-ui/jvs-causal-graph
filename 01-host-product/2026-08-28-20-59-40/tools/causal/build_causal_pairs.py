#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""S4 全量判定·候选边预筛器(393 真实事件)。

策略(保守覆盖,PoC 口径):
- 每个 change 事件(方案/规则变更)与同 corpus 内 [before_ts-7d, after_ts+7d] 窗口内的
  decision/event/assign 事件配成候选(from 时间 ≤ to 时间);
- 同主体关键词重叠(共指)的候选排前;
- 上限 N 对,按「变更中心度」排序输出。
输出:候选对 jsonl(每行 {from_event, to_event, from_ts, to_ts, subject 共指})。
"""

import argparse
import io
import json
from datetime import datetime, timedelta

CHANGE_TYPES = {"change", "bugfix"}
PAIR_TYPES = {"decision", "meeting", "assign", "schedule", "event", "change"}


def norm(s):
    import re
    return re.sub(r"[\s（）()【】\[\],。·、:：\-—/\\]", "", s or "")


def day(ts):
    try:
        return datetime.strptime(ts, "%Y-%m-%d")
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-pairs", type=int, default=240)
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--shared-only", action="store_true",
                    help="只保留共指(主体/对象共享 ≥4 字连续片段)或时间窗 ≤2 天的候选")
    args = ap.parse_args()

    evs = []
    with io.open(args.events, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                evs.append(json.loads(line))
    by_id = {e["event_id"]: e for e in evs}

    pairs = []
    for c in evs:
        if c.get("type") not in CHANGE_TYPES:
            continue
        cd = day(c.get("ts") or "")
        cn = norm(c.get("subject")) + norm(c.get("object"))
        for o in evs:
            if o["event_id"] == c["event_id"] or o.get("type") not in PAIR_TYPES:
                continue
            od = day(o.get("ts") or "")
            # 时间约束:未知时间仅允许其作为 from
            if cd and od:
                if not (od <= cd + timedelta(days=args.window_days) and od >= cd - timedelta(days=args.window_days)):
                    continue
            elif not od:
                continue
            if od and cd and od > cd:
                continue
            on = norm(o.get("subject")) + norm(o.get("object"))
            # 共指:共享 ≥4 字连续片段
            shared4 = any(a1 and b1 and (min(len(a1), len(b1)) >= 4 and (a1 in b1 or b1 in a1))
                          for a1, b1 in [(cn, on)])
            shared = bool(set(cn) & set(on))  # 粗略共指(任意单字重叠)
            if args.shared_only:
                if not (shared4 or shared and abs((cd - od).days if cd and od else 99) <= 2):
                    continue
            pairs.append((0 if (shared4 or shared) else 1, o["event_id"], c["event_id"]))

    pairs.sort(key=lambda x: (x[0], x[1], x[2]))
    picked = pairs[: args.max_pairs]
    with io.open(args.out, "w", encoding="utf-8") as f:
        for _, fm, to in picked:
            f.write(json.dumps({"from_event": fm, "to_event": to,
                                "from_ts": by_id[fm].get("ts"), "to_ts": by_id[to].get("ts"),
                                "from_subj": by_id[fm].get("subject"), "to_subj": by_id[to].get("subject")},
                               ensure_ascii=False) + "\n")
    print(f"候选对: {len(picked)} / 全量候选 {len(pairs)} → {args.out}")


if __name__ == "__main__":
    raise SystemExit(main())
