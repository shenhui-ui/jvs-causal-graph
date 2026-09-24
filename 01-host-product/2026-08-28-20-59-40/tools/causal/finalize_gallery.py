# -*- coding: utf-8 -*-
"""M1 正式图库定版:以 v5 边为基准 + v6 交叉核对。

口径(2026-09-02 定):
  - 定版图库 = v5(GLM-5.2) 全量边;v6(flash-lite) 仅作交叉核对,不并入;
  - 冲突边(同一 (from_event,to_event) 上 type 或 strength 与 v6 不一致)一律保留 v5,
    并单独记录到冲突观察表(见定版说明 md §四);
  - v6 独有边(仅 v6 判定)不纳入定版,仅统计观察。

输出(写新文件,不覆盖旧文件):
  - docs/host_memory_dump/all-real-edges-finalized.jsonl    定版 jsonl(284 边,字段与 v5 一致)
  - docs/host_memory_dump/M1-正式图库定版说明-20260902.md    定版说明 md(含冲突观察表)
  - docs/host_memory_dump/finalize-summary-20260902.json    机器可读摘要(便于 read 复核)
"""
import collections
import io
import json
import sys

BASE = r"C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"
V5 = BASE + r"\all-real-edges-v5.jsonl"
V6 = BASE + r"\all-real-edges-v6.jsonl"
OLD = BASE + r"\all-real-edges-final.jsonl"
OUT_EDGES = BASE + r"\all-real-edges-finalized.jsonl"
OUT_MD = BASE + r"\M1-正式图库定版说明-20260902.md"
OUT_SUM = BASE + r"\finalize-summary-20260902.json"


def load(path):
    rows = []
    with io.open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception as e:  # noqa: BLE001
                raise SystemExit("JSON 解析失败 %s 第 %d 行: %s" % (path, i, e))
    return rows


def key(e):
    return (e["from_event"], e["to_event"])


def dist(rows, field):
    c = collections.Counter(r.get(field) for r in rows)
    return dict(sorted(c.items(), key=lambda x: (-x[1], str(x[0]))))


def fmt_dist(d):
    return " / ".join("%s %d" % (k, v) for k, v in d.items())


def main():
    v5 = load(V5)
    v6 = load(V6)
    old = load(OLD)

    d5 = {key(e): e for e in v5}
    d6 = {key(e): e for e in v6}
    dold = {key(e): e for e in old}

    assert len(d5) == len(v5), "v5 存在重复 (from_event,to_event)"
    assert len(d6) == len(v6), "v6 存在重复 (from_event,to_event)"
    assert len(dold) == len(old), "old final 存在重复 (from_event,to_event)"

    # ---- v5 / v6 交叉核对 ----
    common = sorted(set(d5) & set(d6), key=lambda k: (k[0], k[1]))
    conflicts = []  # (v5_edge, v6_edge, dims)
    agreed = []
    for k in common:
        a, b = d5[k], d6[k]
        dims = []
        if a["type"] != b["type"]:
            dims.append("type")
        if a["strength"] != b["strength"]:
            dims.append("strength")
        if dims:
            conflicts.append((a, b, dims))
        else:
            agreed.append((a, b))

    v5_only = sorted(set(d5) - set(d6), key=lambda k: (k[0], k[1]))
    v6_only = sorted(set(d6) - set(d5), key=lambda k: (k[0], k[1]))

    # ---- 与旧 final 差异(按 from,to)----
    old_keys = set(dold)
    new_keys = set(d5)
    common_on = sorted(old_keys & new_keys, key=lambda k: (k[0], k[1]))
    dropped = sorted(old_keys - new_keys, key=lambda k: (k[0], k[1]))
    added = sorted(new_keys - old_keys, key=lambda k: (k[0], k[1]))
    changed = []  # 新旧共有但 type/strength 变化
    for k in common_on:
        a, b = d5[k], dold[k]
        if a["type"] != b["type"] or a["strength"] != b["strength"]:
            changed.append((a, b))

    # ---- 写定版 jsonl(v5 全量,保持 v5 字段顺序与内容)----
    finalized = v5  # 定版 = v5 全量,冲突边保留 v5
    with io.open(OUT_EDGES, "w", encoding="utf-8", newline="\n") as f:
        for e in finalized:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    # ---- 机器可读摘要 ----
    summary = {
        "generated": "2026-09-02",
        "final_file": OUT_EDGES,
        "v5_total": len(v5),
        "v6_total": len(v6),
        "old_final_total": len(old),
        "common_v5_v6": len(common),
        "agreed": len(agreed),
        "conflicts": len(conflicts),
        "v5_only": len(v5_only),
        "v6_only": len(v6_only),
        "old_new_common": len(common_on),
        "dropped": len(dropped),
        "added": len(added),
        "changed_attrs_old_new": len(changed),
        "v5_strength": dist(v5, "strength"),
        "v6_strength": dist(v6, "strength"),
        "old_strength": dist(old, "strength"),
        "v5_type": dist(v5, "type"),
        "v6_type": dist(v6, "type"),
        "old_type": dist(old, "type"),
        "conflict_dims": dict(collections.Counter(
            ",".join(c[2]) for c in conflicts)),
    }
    with io.open(OUT_SUM, "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # ---- 写定版说明 md ----
    lines = []
    lines.append("# M1 正式图库定版说明(2026-09-02)")
    lines.append("")
    lines.append("> 定版口径:**v5(GLM-5.2,激进型)为主 + v6(sensenova-6.8-flash-lite,克制型)交叉核对**;")
    lines.append("> 冲突边(同一 `from_event→to_event` 上 type 或 strength 与 v6 不一致)一律**保留 v5**,单独记录于 §四冲突观察表;")
    lines.append("> 旧库对比参照 `all-real-edges-final.jsonl`(v3 弱边库),见 §三。")
    lines.append("")
    lines.append("## 一、定版图库概要")
    lines.append("")
    lines.append("- **定版文件**:`all-real-edges-finalized.jsonl`(新文件,未覆盖旧 `all-real-edges-final.jsonl`)")
    lines.append("- **边总数**:%d(= v5 全量;v6 仅交叉核对,不并入定版)" % len(finalized))
    lines.append("- v5 边数:%d ｜ v6 边数:%d ｜ 旧 final 边数:%d" % (len(v5), len(v6), len(old)))
    lines.append("- v5∩v6 共同边:%d" % len(common))
    lines.append("  - 一致(type 与 strength 均同):%d" % len(agreed))
    lines.append("  - **冲突(type 或 strength 不一致):%d** ← 保留 v5,见 §四" % len(conflicts))
    lines.append("- 仅 v5 有(v6 未判,保留):%d" % len(v5_only))
    lines.append("- 仅 v6 有(v5 未判,**不纳入定版**,观察):%d" % len(v6_only))
    lines.append("")
    lines.append("## 二、强度 / 类型分布")
    lines.append("")
    lines.append("| 版本 | 边数 | strength | type |")
    lines.append("|---|---|---|---|")
    lines.append("| v5(定版基准) | %d | %s | %s |" % (len(v5), fmt_dist(dist(v5, "strength")), fmt_dist(dist(v5, "type"))))
    lines.append("| v6(对照) | %d | %s | %s |" % (len(v6), fmt_dist(dist(v6, "strength")), fmt_dist(dist(v6, "type"))))
    lines.append("| 旧 final(v3) | %d | %s | %s |" % (len(old), fmt_dist(dist(old, "strength")), fmt_dist(dist(old, "type"))))
    lines.append("")
    lines.append("## 三、与旧 all-real-edges-final.jsonl 的差异")
    lines.append("")
    lines.append("按 `(from_event, to_event)` 对齐(edge_id 各版独立编号,不作对齐键):")
    lines.append("")
    lines.append("| 对比项 | 数量 |")
    lines.append("|---|---|")
    lines.append("| 旧 final 边数 | %d |" % len(old))
    lines.append("| 定版边数 | %d |" % len(finalized))
    lines.append("| 新旧共有(同 from→to) | %d |" % len(common_on))
    lines.append("| 新增(旧无、定版有) | %d |" % len(added))
    lines.append("| 删除(旧有、定版无) | %d |" % len(dropped))
    lines.append("| 共有但 type/strength 变化 | %d |" % len(changed))
    lines.append("")
    lines.append("差异根源:旧 `all-real-edges-final.jsonl` 为 v3 弱边库(549 边,weak 543/strong 6),")
    lines.append("定版基于 v2.1 提示词 + GLM-5.2 重判的 v5(284 边,strong %d),强度扁平问题已解决;"
                 % dist(v5, "strength").get("strong", 0))
    lines.append("故边数大幅收敛(549→284),新增 %d 条、删除 %d 条。"
                 % (len(added), len(dropped)))
    lines.append("")
    lines.append("## 四、v5/v6 冲突观察表(%d 条)" % len(conflicts))
    lines.append("")
    lines.append("> 冲突 = 同一 `from→to` 上 v5 与 v6 的 type 或 strength 不一致。**定版一律保留 v5 判定**,")
    lines.append("> 本表仅供人工/审计对照。")
    lines.append("")
    lines.append("| # | from | to | v5 edge | v5 type | v5 strength | v5 conf | v6 edge | v6 type | v6 strength | v6 conf | 冲突维度 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, (a, b, dims) in enumerate(conflicts, 1):
        lines.append("| %d | %s | %s | %s | %s | %s | %.2f | %s | %s | %s | %.2f | %s |"
                     % (i, a["from_event"], a["to_event"], a["edge_id"], a["type"], a["strength"],
                        a["confidence"], b["edge_id"], b["type"], b["strength"], b["confidence"],
                        "+".join(dims)))
    lines.append("")
    lines.append("## 五、结论")
    lines.append("")
    lines.append("- 正式图库定版 = **`all-real-edges-finalized.jsonl`**(%d 边,strong %d,supersede %d),"
                 % (len(finalized), dist(v5, "strength").get("strong", 0), dist(v5, "type").get("supersede", 0)))
    lines.append("  后续 M1-10 正式基线、图库入库(causal_graph.py ingest)以本文件为准;")
    lines.append("- v6 独有边 %d 条未并入(仅观察);如后续人工仲裁需增补,可依 §四对照补判;" % len(v6_only))
    lines.append("- 编造边审计已闭环(67 关键对 0 编造),与本次定版无冲突。")
    lines.append("")
    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))

    print("OK finalized=%d conflicts=%d v6_only=%d old=%d" % (len(finalized), len(conflicts), len(v6_only), len(old)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
