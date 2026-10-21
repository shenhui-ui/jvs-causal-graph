"""Offline checks for the `ingest_edges` confidence gate; no credentials, production DB or network.

判据纪律: **能拒绝才算校验** —— 每个正向断言都配一个「篡改输入 ⇒ 结论必须翻转」的负向用例,
否则就是恒真探针。
"""
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import causal_graph as cg


def stats_new():
    return {"inserted": 0, "skipped_duplicate": 0, "skipped_missing_endpoint": 0,
            "skipped_no_id": 0, "relabeled_pending": 0, "gated_conf_none": 0,
            "gated_strength_conflict": 0, "gated_strength_missing": 0}


def edge(conf, strength, eid="E1"):
    return {"id": eid, "from_event": "a", "to_event": "b", "type": "cause",
            "confidence": conf, "strength": strength}


class NormalizeTests(unittest.TestCase):
    """`_normalize_edge`：按 confidence 归一 strength 标签。"""

    def norm(self, conf, strength):
        st = stats_new()
        return cg._normalize_edge(edge(conf, strength), st), st

    def test_low_conf_strong_is_relabelled_and_counted_as_conflict(self):
        out, st = self.norm(0.6, "strong")
        self.assertEqual(out, "pending")
        self.assertEqual(st["relabeled_pending"], 1)
        self.assertEqual(st["gated_strength_conflict"], 1)

    def test_missing_confidence_becomes_pending(self):
        for bad in (None, "", "abc"):
            out, st = self.norm(bad, "strong")
            self.assertEqual(out, "pending", "conf=%r 应改标" % (bad,))
            self.assertEqual(st["gated_conf_none"], 1)

    def test_boundary_above_threshold_is_preserved(self):
        out, st = self.norm(0.61, "strong")
        self.assertEqual(out, "strong")
        self.assertEqual(st["relabeled_pending"], 0)

    def test_low_conf_weak_is_relabelled_without_conflict(self):
        out, st = self.norm(0.6, "weak")
        self.assertEqual(out, "pending")
        self.assertEqual(st["relabeled_pending"], 1)
        self.assertEqual(st["gated_strength_conflict"], 0,
                         "weak 本就不参与多跳，不算冲突")

    def test_already_pending_is_idempotent(self):
        out, st = self.norm(0.6, "pending")
        self.assertEqual(out, "pending")
        self.assertEqual(st["relabeled_pending"], 0)

    def test_medium_is_flattened_to_strong(self):
        out, st = self.norm(0.9, "medium")
        self.assertEqual(out, "strong", "medium 未出现在边集，须压平以对齐 _edge_expandable")

    def test_missing_strength_is_recorded(self):
        out, st = self.norm(0.9, None)
        self.assertEqual(out, "strong")
        self.assertEqual(st["gated_strength_missing"], 1)

    def test_threshold_is_actually_read_not_hardcoded(self):
        """反恒真：挪动阈值，同一输入必须给出不同结论。"""
        saved = cg.CONF_GATE
        try:
            cg.CONF_GATE = 0.99
            self.assertEqual(self.norm(0.6, "strong")[0], "pending")
            self.assertEqual(self.norm(0.999, "strong")[0], "strong")
            cg.CONF_GATE = 0.0
            self.assertEqual(self.norm(0.6, "strong")[0], "strong")
        finally:
            cg.CONF_GATE = saved


class ExpandabilityInvarianceTests(unittest.TestCase):
    """改标**不得**改变可展开性（这是「增量堵口零可达性影响」的机制证明）。"""

    CASES = [(0.6, "strong"), (0.6, "weak"), (None, "strong"), (0.61, "strong"),
             (0.9, "weak"), (0.61, "weak"), (0.35, "strong"), (0.99, "strong"),
             (1.0, "strong"), (0.6, "pending"), (0.0, "strong"), (0.9, "medium")]

    def test_expandable_unchanged_before_and_after(self):
        for conf, s0 in self.CASES:
            st = stats_new()
            new_s = cg._normalize_edge(edge(conf, s0), st)
            before = cg._edge_expandable({"confidence": conf, "strength": s0})
            after = cg._edge_expandable({"confidence": conf, "strength": new_s})
            self.assertEqual(before, after,
                             "conf=%r strength=%r 改标后展开性改变" % (conf, s0))

    def test_only_high_conf_edges_are_expandable(self):
        """正向基线：必须真的存在可展开的边，否则上面的等价式是恒真。"""
        self.assertTrue(cg._edge_expandable({"confidence": 0.9, "strength": "strong"}))
        self.assertFalse(cg._edge_expandable({"confidence": 0.6, "strength": "strong"}))
        self.assertFalse(cg._edge_expandable({"confidence": 0.9, "strength": "weak"}))
        self.assertFalse(cg._edge_expandable({"confidence": None, "strength": "strong"}))


class IngestGateTests(unittest.TestCase):
    """`ingest_edges` 端到端：改标而非 skip。"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="causal-gate-test-")
        self.addCleanup(folder.cleanup)
        self.db = os.path.join(folder.name, "t.db")
        cg.init_db(self.db)
        con = cg.connect(self.db)
        try:
            cur = con.cursor()
            for eid in ("a", "b"):
                cur.execute(
                    "INSERT INTO events(id, ts, type, subject, action, object) "
                    "VALUES(?,?,?,?,?,?)", (eid, "2026-01-0%d" % (1 if eid == "a" else 2),
                                            "x", "s", "a", "o"))
            con.commit()
        finally:
            con.close()

    def ingest(self, edges_cfg, gate):
        saved = os.environ.get("JVS_CONF_GATE")
        os.environ["JVS_CONF_GATE"] = gate
        try:
            con = cg.connect(self.db)
            try:
                cur = con.cursor()
                st = cg.ingest_edges(cur, edges_cfg)
                con.commit()
            finally:
                con.close()
        finally:
            if saved is None:
                os.environ.pop("JVS_CONF_GATE", None)
            else:
                os.environ["JVS_CONF_GATE"] = saved
        return st

    def strength_in_db(self, eid):
        con = cg.connect(self.db)
        try:
            return con.execute("SELECT strength FROM causal_edges WHERE id=?",
                               (eid,)).fetchone()["strength"]
        finally:
            con.close()

    def test_gate_on_relabels_but_never_drops(self):
        st = self.ingest([edge(0.6, "strong", "G1")], "1")
        self.assertEqual(st["inserted"], 1, "闸门不得 skip：边仍须入库")
        self.assertEqual(self.strength_in_db("G1"), "pending")
        self.assertEqual(st["relabeled_pending"], 1)
        self.assertEqual(st["gated_strength_conflict"], 1)

    def test_gate_off_preserves_legacy_behaviour(self):
        """开关可回退 ⇒ 证明这不是硬编码行为。"""
        st = self.ingest([edge(0.6, "strong", "G2")], "0")
        self.assertEqual(st["inserted"], 1)
        self.assertEqual(self.strength_in_db("G2"), "strong")
        self.assertEqual(st["relabeled_pending"], 0)

    def test_low_conf_edge_still_present_for_one_hop_clue(self):
        """低置信边改标后**仍可查得** —— 保住「一跳线索」用途。"""
        self.ingest([edge(0.6, "strong", "G3")], "1")
        con = cg.connect(self.db)
        try:
            n = con.execute("SELECT COUNT(*) c FROM causal_edges WHERE id='G3'"
                            ).fetchone()["c"]
            self.assertEqual(n, 1)
        finally:
            con.close()

    def test_high_conf_edge_untouched(self):
        self.ingest([edge(0.85, "strong", "G4")], "1")
        self.assertEqual(self.strength_in_db("G4"), "strong")

    def test_missing_endpoint_still_skipped_by_older_rule(self):
        """闸门不得掩盖既有跳过条件。"""
        st = self.ingest([{"id": "G5", "from_event": "a", "to_event": "zz",
                           "type": "cause", "confidence": 0.9, "strength": "strong"}], "1")
        self.assertEqual(st["skipped_missing_endpoint"], 1)
        self.assertEqual(st["inserted"], 0)

    def test_negative_no_id_still_skipped(self):
        st = self.ingest([{"from_event": "a", "to_event": "b", "type": "cause",
                           "confidence": 0.9, "strength": "strong"}], "1")
        self.assertEqual(st["skipped_no_id"], 1)
        self.assertEqual(st["inserted"], 0)


class WeakExpandFlagTests(unittest.TestCase):
    """2026-09-28 裁定 Q-2：`weak` 可继续多跳（**opt-in，默认关**）。

    判据纪律：**默认关时必须与旧实现逐值等价** —— 否则「默认关」是空话，
    而它正是「本裁定不自动改生产行为」的唯一保障。
    """

    STRENGTHS = ("strong", "weak", "pending", "medium", None)
    CONFS = (0.9, 0.61, 0.6, 0.5, 0.35, None, "abc", "")

    @staticmethod
    def legacy(edge):
        """改动前的实现（逐字重放，作负向对照）。"""
        if edge.get("strength") in ("weak", "pending"):
            return False
        confidence = edge.get("confidence")
        if confidence is None:
            return False
        try:
            return float(confidence) > 0.6
        except (TypeError, ValueError):
            return False

    def setUp(self):
        self._saved = os.environ.get("JVS_WEAK_EXPAND")
        os.environ.pop("JVS_WEAK_EXPAND", None)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            os.environ.pop("JVS_WEAK_EXPAND", None)
        else:
            os.environ["JVS_WEAK_EXPAND"] = self._saved

    def _cases(self):
        for s in self.STRENGTHS:
            for c in self.CONFS:
                yield {"strength": s, "confidence": c}

    def test_default_off_equals_legacy_on_every_case(self):
        """⭐ 核心：默认关 ⇒ 与旧实现**逐值等价**（否则就是悄悄改了行为）。"""
        for edge in self._cases():
            self.assertEqual(cg._edge_expandable(edge), self.legacy(edge),
                             "默认关时与旧实现不等价: %r" % (edge,))

    def test_explicit_zero_also_equals_legacy(self):
        """`JVS_WEAK_EXPAND=0` 必须**真的关**（假值陷阱同族）。"""
        os.environ["JVS_WEAK_EXPAND"] = "0"
        for edge in self._cases():
            self.assertEqual(cg._edge_expandable(edge), self.legacy(edge),
                             "=0 时与旧实现不等价: %r" % (edge,))

    def test_empty_also_equals_legacy(self):
        os.environ["JVS_WEAK_EXPAND"] = ""
        for edge in self._cases():
            self.assertEqual(cg._edge_expandable(edge), self.legacy(edge))

    def test_flag_on_makes_weak_expandable(self):
        """正向：开关打开后 weak 高置信边**必须**变为可扩展。"""
        os.environ["JVS_WEAK_EXPAND"] = "1"
        self.assertTrue(cg._edge_expandable({"strength": "weak", "confidence": 0.9}))
        self.assertTrue(cg._edge_expandable({"strength": "weak", "confidence": 0.61}))

    def test_flag_on_still_blocks_low_confidence_weak(self):
        """放开 weak **不等于**放开低置信：conf ≤ 0.6 仍不可扩展。"""
        os.environ["JVS_WEAK_EXPAND"] = "1"
        self.assertFalse(cg._edge_expandable({"strength": "weak", "confidence": 0.6}))
        self.assertFalse(cg._edge_expandable({"strength": "weak", "confidence": 0.5}))
        self.assertFalse(cg._edge_expandable({"strength": "weak", "confidence": None}))

    def test_pending_never_expandable_even_when_on(self):
        """⚠️ `pending` 语义是「不可信」，**不在**本开关范围内。"""
        os.environ["JVS_WEAK_EXPAND"] = "1"
        for c in self.CONFS:
            self.assertFalse(cg._edge_expandable({"strength": "pending", "confidence": c}),
                             "pending 不得因本开关而可扩展: conf=%r" % (c,))

    def test_tamper_flag_changes_conclusion(self):
        """反恒真：挪动开关，同一输入必须换结论。"""
        edge = {"strength": "weak", "confidence": 0.9}
        off = cg._edge_expandable(edge)
        os.environ["JVS_WEAK_EXPAND"] = "1"
        on = cg._edge_expandable(edge)
        self.assertNotEqual(off, on, "开关挪动后结论不变 ⇒ 开关没被读")


class EnvFlagSingleSourceTests(unittest.TestCase):
    """三态解析必须**只有一份实现**（红线 12 同族：多份复制会静默分叉）。"""

    def test_both_modules_share_one_function_object(self):
        import llm_answer
        self.assertIs(llm_answer.env_flag, cg.env_flag,
                      "两处解析不是同一对象 ⇒ 已被复制成两份实现")


if __name__ == "__main__":
    unittest.main(verbosity=2)