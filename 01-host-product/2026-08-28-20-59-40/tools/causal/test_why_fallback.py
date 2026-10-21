# -*- coding: utf-8 -*-
"""M1-11 why() 降级分类器的离线检查；无凭证、无网络、不碰生产图谱。

判据纪律（**能拒绝才算校验**）
------------------------------
每个正向断言都配一个「**篡改输入 ⇒ 结论必须翻转**」的负向用例。
只证明「会通过」的断言是恒真探针，不算校验。

另有一类专门的「配置驱动」用例：改 `fallback.yaml` 的阈值 ⇒ 同一输入必须换类。
这同时是任务 S1 的验收条件（阈值必须**真的被读取**，不是硬编码）。
"""

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import miniyaml
import why_fallback as wf

REAL_YAML = HERE / "fallback.yaml"


def chain(edges, start="e0"):
    """构造一个 `why()` 形状的返回值（与 causal_graph.why 的输出字段对齐）。"""
    return {
        "ok": True,
        "start_event": start,
        "max_hops": 3,
        "events": [{"id": start}],
        "edges": edges,
        "skipped_type_reverse": 0,
    }


def e(frm, to, conf, strength, expandable, direction="forward", eid=None):
    return {
        "edge_id": eid or ("%s->%s" % (frm, to)),
        "from_event": frm,
        "to_event": to,
        "type": "cause",
        "strength": strength,
        "rationale": "r",
        "confidence": conf,
        "source_hash": "h",
        "direction": direction,
        "expandable": expandable,
        "from_evidence_ref": None,
        "to_evidence_ref": None,
    }


def write_cfg(text, folder):
    path = os.path.join(folder, "fallback.yaml")
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def read_real_yaml():
    """读真实 fallback.yaml 的原文（供「改一处再比对」的配置驱动用例）。"""
    with io.open(str(REAL_YAML), encoding="utf-8") as f:
        return f.read()


class MiniYamlRejectionTests(unittest.TestCase):
    """加载器必须**拒绝**读不懂的东西，而不是静默给出半个配置。"""

    def test_real_config_parses(self):
        cfg = miniyaml.load(str(REAL_YAML))
        self.assertIsInstance(cfg, dict)
        self.assertIn("classes", cfg)
        self.assertIn("precedence", cfg)

    def test_rejects_duplicate_key(self):
        with self.assertRaises(ValueError):
            miniyaml.loads("a: 1\na: 2\n")

    def test_rejects_tab_indent(self):
        with self.assertRaises(ValueError):
            miniyaml.loads("a:" + chr(10) + chr(9) + "b: 1")

    def test_rejects_odd_indent(self):
        with self.assertRaises(ValueError):
            miniyaml.loads("a: 1" + chr(10) + " b: 2")

    def test_rejects_block_scalar_and_flow_mapping(self):
        for bad in ("a: |" + chr(10) + "  x", "a: {x: 1}", "a: &anc 1", "---"):
            with self.assertRaises(ValueError, msg="未拒绝 %r" % bad):
                miniyaml.loads(bad)

    def test_rejects_empty_key_and_missing_colon(self):
        for bad in ("a: 1" + chr(10) + ": 2", "a: 1" + chr(10) + "just text"):
            with self.assertRaises(ValueError, msg="未拒绝 %r" % bad):
                miniyaml.loads(bad)

    def test_mapping_items_in_list(self):
        got = miniyaml.loads("k:" + chr(10) + "  - id: A" + chr(10) + "    q: 1" + chr(10))
        self.assertEqual(got, {"k": [{"id": "A", "q": 1}]})

    def test_colon_scalar_not_treated_as_mapping(self):
        got = miniyaml.loads("k:" + chr(10) + "  - http://x" + chr(10))
        self.assertEqual(got, {"k": ["http://x"]})


class ConfigValidationTests(unittest.TestCase):
    """残缺配置必须被拒绝 —— 否则「单一真值源」会退化成「默认值」。"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="m1-11-cfg-")
        self.addCleanup(folder.cleanup)
        self.dir = folder.name
        self.raw = read_real_yaml()

    def cfg_path(self, text):
        return write_cfg(text, self.dir)

    def test_missing_class_rejected(self):
        text = self.raw.replace("  weak_only:", "  weak_only_DISABLED:")
        with self.assertRaises(ValueError):
            wf.load_config(self.cfg_path(text))

    def test_missing_precedence_rejected(self):
        text = self.raw.replace("precedence: [no_chain, low_confidence, weak_only]",
                                "precedence_order: [no_chain]")
        with self.assertRaises(ValueError):
            wf.load_config(self.cfg_path(text))

    def test_unknown_class_in_precedence_rejected(self):
        text = self.raw.replace("precedence: [no_chain, low_confidence, weak_only]",
                                "precedence: [no_chain, low_confidence, weak_only, typo_class]")
        with self.assertRaises(ValueError):
            wf.load_config(self.cfg_path(text))

    def test_bad_metric_rejected(self):
        text = self.raw.replace("metric: max_confidence", "metric: median_confidence")
        cfg = wf.load_config(self.cfg_path(text))
        with self.assertRaises(ValueError):
            wf.classify(chain([e("e0", "e1", 0.9, "strong", True)]), cfg)

    def test_bad_min_hops_rejected(self):
        text = self.raw.replace("min_hops: 2", "min_hops: 0")
        cfg = wf.load_config(self.cfg_path(text))
        with self.assertRaises(ValueError):
            wf.classify(chain([e("e0", "e1", 0.9, "weak", True)]), cfg)


class MeasureTests(unittest.TestCase):
    """测度提取必须能拒绝失败输入，而不是返回「全 0」。"""

    def test_failed_why_is_rejected(self):
        with self.assertRaises(ValueError):
            wf.measure({"ok": False, "error": "event not found: X"})

    def test_non_dict_is_rejected(self):
        with self.assertRaises(ValueError):
            wf.measure(None)

    def test_measures_on_strong_chain(self):
        m = wf.measure(chain([e("e0", "e1", 0.9, "strong", True),
                              e("e1", "e2", 0.8, "strong", True)]))
        self.assertEqual(m["n_edges"], 2)
        self.assertEqual(m["n_expandable"], 2)
        self.assertEqual(m["max_confidence"], 0.9)
        self.assertEqual(m["min_confidence"], 0.8)
        self.assertEqual(m["hops"], 2)
        self.assertFalse(m["all_weak"])

    def test_hops_follow_backward_edges(self):
        """反向边的下一跳是 from_event —— 深度重放必须尊重 direction。

        构造：起点 e0；反向边 e1→e0（即 to_event == 起点，故从 backward[e0] 取出，
        下一跳 = from_event = e1）；再由 e1 经正向边到 e2 ⇒ 深度应为 2。
        """
        m = wf.measure(chain([e("e1", "e0", 0.9, "strong", True, direction="backward"),
                              e("e1", "e2", 0.9, "strong", True, direction="forward")],
                             start="e0"))
        self.assertEqual(m["hops"], 2)

    def test_unparsable_confidence_counted(self):
        m = wf.measure(chain([e("e0", "e1", "abc", "strong", True)]))
        self.assertEqual(m["n_unknown_confidence"], 1)
        self.assertIsNone(m["max_confidence"])


class PositiveClassTests(unittest.TestCase):
    """三类各至少一个正例 —— 且必须存在**不降级**的基线，排除「判据过宽」。"""

    def setUp(self):
        self.cfg = wf.load_config()

    def test_no_chain(self):
        out = wf.classify(chain([]), self.cfg)
        self.assertEqual(out["degradation_class"], "no_chain")
        self.assertIn("0 条边", out["statement"])

    def test_low_confidence(self):
        out = wf.classify(chain([e("e0", "e1", 0.55, "strong", True)]), self.cfg)
        self.assertEqual(out["degradation_class"], "low_confidence")
        self.assertIn("0.55", out["statement"])
        self.assertIn("0.6", out["statement"], "阈值须来自配置并渲染进陈述")

    def test_weak_only(self):
        """跳数达标时的弱关联（合成输入：weak 边被标为可扩展）。"""
        out = wf.classify(chain([e("e0", "e1", 0.9, "weak", True),
                                 e("e1", "e2", 0.9, "weak", True)]), self.cfg)
        self.assertEqual(out["degradation_class"], "weak_only")
        self.assertIn("2 跳", out["statement"])

    def test_no_degradation_baseline(self):
        """强边高置信 ⇒ 不降级。这条防的是「什么都判降级」。"""
        out = wf.classify(chain([e("e0", "e1", 0.9, "strong", True),
                                 e("e1", "e2", 0.85, "strong", True)]), self.cfg)
        self.assertEqual(out["degradation_class"], "none")
        self.assertIsNone(out["statement"])
        self.assertEqual(out["all_matched"], [])

    def test_weak_only_hop_requirement_is_flagged_unreachable(self):
        """真实遍历形态（weak 边不可扩展）：跳数不达标 ⇒ 告警而非判类。

        这正是「定义与实现不相容」的现场兑现：要求 ≥2 跳，但 weak 边永不入队。
        ⚠️ 文案须**指向开关**（`JVS_WEAK_EXPAND`）—— 不得写成「结构性不可达」的空洞结论
        （2026-09-28 审核 P2-2：weak 类在本裁定后**已可达**，旧文案会误导）。
        """
        out = wf.classify(chain([e("e0", "e1", 0.9, "weak", False)]), self.cfg)
        self.assertNotEqual(out["degradation_class"], "weak_only")
        self.assertTrue(any("不可达" in w for w in out["warnings"]),
                        "应给出「该跳数要求不可达」告警，实得 %r" % (out["warnings"],))
        self.assertTrue(any("JVS_WEAK_EXPAND" in w for w in out["warnings"]),
                        "告警必须指明可达路径（开关名），实得 %r" % (out["warnings"],))

    def test_overlap_is_visible_not_hidden(self):
        """同时命中低置信与弱关联时，优先级选一个，但 all_matched 必须暴露重叠。"""
        out = wf.classify(chain([e("e0", "e1", 0.5, "weak", True),
                                 e("e1", "e2", 0.5, "weak", True)]), self.cfg)
        self.assertEqual(out["degradation_class"], "low_confidence")
        self.assertEqual(out["all_matched"], ["low_confidence", "weak_only"])

    def test_unparsable_confidence_falls_to_low_confidence(self):
        out = wf.classify(chain([e("e0", "e1", None, "strong", True)]), self.cfg)
        self.assertEqual(out["degradation_class"], "low_confidence")
        self.assertTrue(any("不可解析" in w for w in out["warnings"]))


class TamperTests(unittest.TestCase):
    """**核心**：逐类篡改输入，判定必须变化（红线 10）。"""

    def setUp(self):
        self.cfg = wf.load_config()

    def test_tamper_no_chain_by_adding_edge(self):
        base = wf.classify(chain([]), self.cfg)
        self.assertEqual(base["degradation_class"], "no_chain")
        tampered = wf.classify(chain([e("e0", "e1", 0.9, "strong", True)]), self.cfg)
        self.assertNotEqual(tampered["degradation_class"], "no_chain",
                            "加了边仍判「无链」⇒ 判据是恒真的")

    def test_tamper_low_confidence_by_raising_conf(self):
        base = wf.classify(chain([e("e0", "e1", 0.55, "strong", True)]), self.cfg)
        self.assertEqual(base["degradation_class"], "low_confidence")
        tampered = wf.classify(chain([e("e0", "e1", 0.95, "strong", True)]), self.cfg)
        self.assertNotEqual(tampered["degradation_class"], "low_confidence",
                            "抬高置信仍判「低置信」⇒ 阈值没被读")

    def test_tamper_weak_only_by_flipping_strength(self):
        edges = [e("e0", "e1", 0.9, "weak", True), e("e1", "e2", 0.9, "weak", True)]
        base = wf.classify(chain(edges), self.cfg)
        self.assertEqual(base["degradation_class"], "weak_only")
        flipped = [dict(x, strength="strong") for x in edges]
        tampered = wf.classify(chain(flipped), self.cfg)
        self.assertNotEqual(tampered["degradation_class"], "weak_only",
                            "把 weak 改成 strong 仍判「弱关联」⇒ 判据是恒真的")

    def test_tamper_baseline_by_lowering_conf(self):
        """反向篡改：把「不降级」改成「降级」也必须生效。"""
        base = wf.classify(chain([e("e0", "e1", 0.9, "strong", True)]), self.cfg)
        self.assertEqual(base["degradation_class"], "none")
        tampered = wf.classify(chain([e("e0", "e1", 0.1, "strong", True)]), self.cfg)
        self.assertEqual(tampered["degradation_class"], "low_confidence")


class ConfigDrivenTests(unittest.TestCase):
    """任务 S1 的验收：阈值必须**真的被读取**（挪动阈值 ⇒ 同一输入换类）。"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="m1-11-thr-")
        self.addCleanup(folder.cleanup)
        self.dir = folder.name
        self.raw = read_real_yaml()
        self.sample = chain([e("e0", "e1", 0.75, "strong", True)])

    def test_threshold_move_changes_class(self):
        default = wf.classify(self.sample, wf.load_config(write_cfg(self.raw, self.dir)))
        self.assertEqual(default["degradation_class"], "none",
                         "0.75 高于默认阈值 0.6 ⇒ 不应降级")

        raised = self.raw.replace("threshold: 0.6", "threshold: 0.8")
        cfg_hi = wf.load_config(write_cfg(raised, self.dir))
        out = wf.classify(self.sample, cfg_hi)
        self.assertEqual(out["degradation_class"], "low_confidence",
                         "阈值抬到 0.8 后同一输入必须换类 ⇒ 阈值确实来自配置")
        self.assertIn("0.8", out["statement"])

    def test_metric_switch_changes_class(self):
        """切换 metric 读数（max ↔ min）必须能改变判定 —— 口径敏感性的机械证据。"""
        edges = [e("e0", "e1", 0.9, "strong", True), e("e1", "e2", 0.2, "strong", True)]
        by_max = wf.classify(chain(edges), wf.load_config(write_cfg(self.raw, self.dir)))
        self.assertEqual(by_max["degradation_class"], "none")

        swapped = self.raw.replace("metric: max_confidence", "metric: min_confidence")
        by_min = wf.classify(chain(edges), wf.load_config(write_cfg(swapped, self.dir)))
        self.assertEqual(by_min["degradation_class"], "low_confidence",
                         "换 metric 后结论翻转 ⇒ 「最优路径边置信」读法确有口径分歧")

    def test_min_hops_move_changes_class(self):
        edges = [e("e0", "e1", 0.9, "weak", True)]
        relaxed = self.raw.replace("min_hops: 2", "min_hops: 1")
        out = wf.classify(chain(edges), wf.load_config(write_cfg(relaxed, self.dir)))
        self.assertEqual(out["degradation_class"], "weak_only")

    def test_precedence_reorder_changes_winner(self):
        """优先级重排必须改变「选了谁」（同时 all_matched 不变）。"""
        edges = [e("e0", "e1", 0.5, "weak", True), e("e1", "e2", 0.5, "weak", True)]
        cfg_a = wf.load_config(write_cfg(self.raw, self.dir))
        reordered = self.raw.replace("precedence: [no_chain, low_confidence, weak_only]",
                                     "precedence: [no_chain, weak_only, low_confidence]")
        cfg_b = wf.load_config(write_cfg(reordered, self.dir))
        a = wf.classify(chain(edges), cfg_a)
        b = wf.classify(chain(edges), cfg_b)
        self.assertEqual(a["degradation_class"], "low_confidence")
        self.assertEqual(b["degradation_class"], "weak_only")
        self.assertEqual(a["all_matched"], b["all_matched"],
                         "重排优先级不应改变「命中了哪些类」")


class RealGraphSmokeTests(unittest.TestCase):
    """对生产图谱跑一次真入口（只读）。DB 不在仓库内，缺失即跳过。"""

    DB = r"C:\Users\<user>\_staging\d10-graph-refill.db"

    def setUp(self):
        if not os.path.exists(self.DB):
            self.skipTest("生产图谱不在本机（%s）" % self.DB)
        import causal_graph as cg
        self.cg = cg
        self.cfg = wf.load_config()

    def test_isolated_event_is_no_chain(self):
        """无任何边的事件 ⇒ 必须判「无链」（正例来自真实数据，非合成）。"""
        import sqlite3
        con = sqlite3.connect(self.DB)
        try:
            row = con.execute(
                "SELECT e.id FROM events e WHERE NOT EXISTS("
                "SELECT 1 FROM causal_edges x WHERE x.from_event=e.id OR x.to_event=e.id)"
                " LIMIT 1").fetchone()
        finally:
            con.close()
        if row is None:
            self.skipTest("该图谱没有孤立事件")
        out = wf.classify(self.cg.why(self.DB, row[0], max_hops=3), self.cfg)
        self.assertEqual(out["degradation_class"], "no_chain")

    def test_real_chain_never_yields_weak_only(self):
        """真实遍历下 weak 边不可扩展 ⇒ 抽样中不应出现 weak_only（结构性结论）。"""
        import sqlite3
        con = sqlite3.connect(self.DB)
        try:
            ids = [r[0] for r in con.execute(
                "SELECT DISTINCT from_event FROM causal_edges LIMIT 40")]
        finally:
            con.close()
        seen = {}
        for eid in ids:
            out = wf.classify(self.cg.why(self.DB, eid, max_hops=3), self.cfg)
            seen[out["degradation_class"]] = seen.get(out["degradation_class"], 0) + 1
        self.assertEqual(seen.get("weak_only", 0), 0,
                         "真实遍历下出现了 weak_only ⇒ 结构性结论被推翻，须复查：%r" % seen)
        self.assertTrue(seen, "抽样为空 ⇒ 该用例是恒真探针")


if __name__ == "__main__":
    unittest.main(verbosity=2)
