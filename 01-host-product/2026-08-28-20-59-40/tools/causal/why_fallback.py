# -*- coding: utf-8 -*-
"""M1-11：why() 降级判据的**显式分类器**（纯标准库）。

背景
----
立项书 §4 任务 M1-11（**必做**）要求把 `why()` 的降级情形分成三类并给出降级陈述，
且阈值由 `fallback.yaml` 锁版。此前本项目**只有底层语义、没有分类器、也没有 yaml**
（核验见 `docs/M1-11-缺口处置方案-20260928.md` §1）。

本模块职责单一
--------------
输入：`causal_graph.why()` 的输出 + 阈值配置（`fallback.yaml`）
输出：`degradation_class` + 降级陈述 + **判定所依据的原始测度**

⚠️ 口径状态（本模块**不裁决口径**）
-----------------------------------
阈值当前取实现闸门 `causal_graph.CONF_GATE = 0.6`，与立项书 §4 的 `< 0.5`
**不一致**；且「弱关联 = 仅 weak 边且 ≥2 跳」与 `_edge_expandable`（weak 边永不扩展）
**不相容**。⇒ 见 `fallback.yaml` 的 `pending_rulings`（Q-1 / Q-2）。
本模块把这些口径**参数化**并把原始测度一并输出，使裁定下达后只改配置、不改逻辑。

判据纪律（**能拒绝才算校验**）
------------------------------
- `measure()` 对 `ok=False` 的输入**抛错**，不返回「全 0 测度」；
- `load_config()` 对缺字段 / 缺类定义的配置**抛错**；
- 未命中的类一律输出 `none`，不得「全都命中」（判据过宽是缺陷，不是鲁棒）。

命令行
------
    python why_fallback.py <db_path> <event_id> [--max-hops 3] [--json]
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import miniyaml

# 三类降级（与 fallback.yaml 的 classes 键必须一致；不一致即配置错误）
CLASSES = ("no_chain", "low_confidence", "weak_only")

# 不降级
NO_DEGRADATION = "none"

# 可展开判据所承认的「非 weak」强度标签（与 causal_graph._edge_expandable 对齐）
_NON_WEAK = ("weak", "pending")


def default_config_path():
    return os.path.join(HERE, "fallback.yaml")


def load_config(path=None):
    """读取并**校验** fallback.yaml。校验不通过即抛错（不静默用默认值）。"""
    path = path or default_config_path()
    cfg = miniyaml.load(path)
    if not isinstance(cfg, dict):
        raise ValueError("fallback.yaml 顶层必须是映射，实得 %r" % type(cfg).__name__)
    for key in ("classes", "precedence"):
        if key not in cfg:
            raise ValueError("fallback.yaml 缺字段：%s" % key)
    if not isinstance(cfg["classes"], dict):
        raise ValueError("fallback.yaml 的 classes 必须是映射")
    for name in CLASSES:
        if name not in cfg["classes"]:
            raise ValueError("fallback.yaml 缺类定义：%s" % name)
        if not isinstance(cfg["classes"][name], dict):
            raise ValueError("fallback.yaml 的 classes.%s 必须是映射" % name)
    known = set(cfg["precedence"])
    unknown = known - set(CLASSES)
    if unknown:
        raise ValueError("fallback.yaml 的 precedence 含未知类：%s" % sorted(unknown))
    missing = set(CLASSES) - known
    if missing:
        raise ValueError("fallback.yaml 的 precedence 缺类：%s" % sorted(missing))
    return cfg


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _max_depth(res):
    """按 `why()` 的遍历规则重放一遍，求实际达到的最大深度（走过的可扩展边数）。

    `why()` 的输出不含每条边的深度，故此处按同一规则重放，得到**精确**深度而非估计。

    ⚠️ 索引键必须与 `direction` 一致（`causal_graph.why` 的候选收集方式）：
       - `forward` 边从 `forward[node]` 取出（即 `from_event == node`），下一跳 = `to_event`；
       - `backward` 边从 `backward[node]` 取出（即 `to_event == node`），下一跳 = `from_event`。
    若统一按 `from_event` 建索引，反向边会被挂到错误的节点上，深度恒为 0。
    """
    edges = res.get("edges") or []
    by_node = {}
    for e in edges:
        key = e.get("to_event") if e.get("direction") == "backward" else e.get("from_event")
        by_node.setdefault(key, []).append(e)
    start = res.get("start_event")
    best, seen = 0, {start}
    stack = [(start, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > best:
            best = depth
        for e in by_node.get(node, []):
            if not e.get("expandable"):
                continue
            nxt = e.get("from_event") if e.get("direction") == "backward" else e.get("to_event")
            if nxt in seen:
                continue
            seen.add(nxt)
            stack.append((nxt, depth + 1))
    return best


def measure(res):
    """从 `why()` 输出提取判定所需的**原始测度**（不含任何口径判断）。"""
    if not isinstance(res, dict) or not res.get("ok"):
        raise ValueError("why() 未成功，拒绝分类：%r" % (
            (res or {}).get("error") if isinstance(res, dict) else res,))

    edges = res.get("edges") or []
    confs = [_as_float(e.get("confidence")) for e in edges]
    known = [c for c in confs if c is not None]
    strengths = [e.get("strength") for e in edges]
    return {
        "n_edges": len(edges),
        "n_events": len(res.get("events") or []),
        "n_expandable": sum(1 for e in edges if e.get("expandable")),
        "max_confidence": max(known) if known else None,
        "min_confidence": min(known) if known else None,
        "n_unknown_confidence": len(confs) - len(known),
        "all_weak": bool(edges) and all(s in _NON_WEAK for s in strengths),
        "hops": _max_depth(res),
    }


def _matched_classes(m, cfg):
    """返回 (命中的类（规范顺序）, 胜出者（按 precedence）, 告警列表)。"""
    classes = cfg["classes"]
    warnings = []
    matched = []

    if classes["no_chain"].get("enabled", True) and m["n_edges"] == 0:
        matched.append("no_chain")

    lc = classes["low_confidence"]
    if lc.get("enabled", True) and m["n_edges"] > 0:
        threshold = _as_float(lc.get("threshold"))
        if threshold is None:
            raise ValueError("fallback.yaml 的 low_confidence.threshold 不可解析")
        metric = lc.get("metric", "max_confidence")
        if metric not in ("max_confidence", "min_confidence"):
            raise ValueError("fallback.yaml 的 low_confidence.metric 非法：%r" % metric)
        value = m.get(metric)
        if value is None:
            warnings.append("置信度全部不可解析（n_unknown_confidence=%d）⇒ 按「低置信」处理"
                            % m["n_unknown_confidence"])
            matched.append("low_confidence")
        elif value < threshold:
            matched.append("low_confidence")

    wo = classes["weak_only"]
    if wo.get("enabled", True) and m["all_weak"]:
        min_hops = wo.get("min_hops", 2)
        if not isinstance(min_hops, int) or min_hops < 1:
            raise ValueError("fallback.yaml 的 weak_only.min_hops 必须是正整数")
        if m["hops"] >= min_hops:
            matched.append("weak_only")
        else:
            warnings.append(
                "仅 weak 边但跳数 %d < min_hops %d；**当前开关状态下** weak 边不可扩展 "
                "⇒ 该跳数要求在 why() 中不可达。"
                "设 `JVS_WEAK_EXPAND=1`（2026-09-28 裁定 Q-2）后 weak 可扩展，该类方可达"
                % (m["hops"], min_hops))

    order = {name: i for i, name in enumerate(cfg["precedence"])}
    # 两件事分开：`all_matched` 用**规范顺序**（与 precedence 无关，只回答「命中了哪些类」）；
    # 胜出者才按 precedence 取第一个。否则「重排优先级」会连带改动命中清单，掩盖真实重叠。
    matched_canonical = [name for name in CLASSES if name in matched]
    winner = min(matched, key=lambda name: order[name]) if matched else NO_DEGRADATION
    return matched_canonical, winner, warnings


def classify(res, cfg=None):
    """把 `why()` 输出分类。

    返回 dict：
        {
          "degradation_class": "no_chain" | "low_confidence" | "weak_only" | "none",
          "all_matched": [...],      # 全部命中的类（可见重叠，不被优先级掩盖）
          "statement": str | None,   # 降级陈述（none 时为 None）
          "measures": {...},         # 原始测度
          "warnings": [...],         # 口径告警（不改变判定）
          "config_status": str,      # fallback.yaml 的 status（draft_pending_ruling / locked）
        }
    """
    cfg = cfg or load_config()
    m = measure(res)
    matched, chosen, warnings = _matched_classes(m, cfg)
    statement = None
    if chosen != NO_DEGRADATION:
        cls_cfg = cfg["classes"][chosen]
        template = cls_cfg.get("statement")
        # ⚠️ 置信度**不可解析**时不能套用常规模板：那会渲染出「置信 None 低于阈值 0.6」
        # 这种用户可见的措辞瑕疵（2026-09-28 审核 P3-2 指出）⇒ 走单独模板。
        if chosen == "low_confidence":
            metric = cls_cfg.get("metric", "max_confidence")
            if m.get(metric) is None:
                template = cls_cfg.get("statement_unknown") or template
        if not template:
            raise ValueError("fallback.yaml 的 classes.%s 缺 statement" % chosen)
        fields = dict(m)
        fields["threshold"] = _as_float(cfg["classes"]["low_confidence"].get("threshold"))
        statement = _render(template, fields)
    return {
        "degradation_class": chosen,
        "all_matched": matched,
        "statement": statement,
        "measures": m,
        "warnings": warnings,
        "config_status": cfg.get("status"),
    }


def _render(template, fields):
    """填充占位符；出现未知占位符即抛错（不静默留空）。"""
    class _Safe(dict):
        def __missing__(self, key):
            raise KeyError("fallback.yaml 的 statement 含未知占位符：{%s}" % key)

    try:
        return template.format_map(_Safe(fields))
    except KeyError:
        raise
    except (IndexError, ValueError) as exc:
        raise ValueError("fallback.yaml 的 statement 占位符不合法：%s" % exc)


def main(argv=None):
    ap = argparse.ArgumentParser(description="M1-11 why() 降级分类器")
    ap.add_argument("db")
    ap.add_argument("event_id")
    ap.add_argument("--max-hops", type=int, default=3)
    ap.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = ap.parse_args(argv)

    import causal_graph as cg

    res = cg.why(args.db, args.event_id, max_hops=args.max_hops)
    out = classify(res)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print("降级类：%s" % out["degradation_class"])
    print("命中类：%s" % (out["all_matched"] or ["none"]))
    print("测度  ：%s" % json.dumps(out["measures"], ensure_ascii=False))
    if out["statement"]:
        print("陈述  ：%s" % out["statement"])
    for w in out["warnings"]:
        print("告警  ：%s" % w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
