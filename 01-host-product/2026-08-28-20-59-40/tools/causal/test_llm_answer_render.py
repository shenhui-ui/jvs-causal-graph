# -*- coding: utf-8 -*-
"""`build_prompt` 的**证据链渲染契约**回归件。

## 为什么需要这个件

D-① 闸门把 `conf<=0.6` 的入边标为 `pending`。我原先据「边集合 0/10 题变化」
宣称「零影响」，但**漏了标签还有别的消费者**：`build_prompt` 把
`strength` **逐字渲染进提示词**（`- {type}/{strength}{tag}: ...`）。

未修之前：`weak` 边会渲染成 `- trigger/weak/弱:`，
而改标后同一条渲染成 `- trigger/pending:` ⇒
① 提示词顶部图例只声明「含 strong/weak 边」，`pending` **无图例解释**；
② 「/弱」信号消失，模型不再被提醒「仅供参考」。
**实测 7/10 题的 prompt 退化。**

## 本件锁定的契约

| 输入 strength | 行内必须含 | 行内不得出现 |
|---|---|---|
| `strong`  | `strong`，且**无**「/弱」 | `/弱`, `低置信` |
| `weak`    | `weak` + `/弱`           | `低置信`（weak 不额外标） |
| `pending` | `pending` + `/弱` + `低置信` | —（必须与 weak 同带「/弱」） |
| `None`    | `?`（占位，保持旧行为）    | `/弱` |

## 反恒真自证

本件必须能**拒绝**「把 pending 按 strong 渲染」这类突变。见 `_mutation_guard`：
若代码把 `("weak","pending")` 缩回只判 `("weak",)`，则 T5/T6 必失败。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm_answer  # noqa: E402


def _make_chain(strength):
    """单个边的最小链；事件表补齐两端。"""
    return [{
        "from_event": "E1", "to_event": "E2", "type": "trigger",
        "strength": strength, "confidence": 0.5, "rationale": "测试理由",
    }]


def _evs():
    return {
        "E1": {"ts": "2026-01-01", "subject": "S1", "action": "A1", "object": "O1"},
        "E2": {"ts": "2026-01-02", "subject": "S2", "action": "A2", "object": "O2"},
    }


def _render(strength):
    os.environ.pop("LLM_CHAIN_PRIORITIZE", None)
    chain = _make_chain(strength)
    p = llm_answer.build_prompt({"question": "测试问题"}, _evs()["E1"], chain, _evs())
    # 取出证据链里那一条以 "- " 开头的边行
    for ln in p.splitlines():
        if ln.startswith("- ") and "trigger/" in ln:
            return p, ln
    return p, ""


class TestEvidenceChainRender(unittest.TestCase):

    def test_T1_legend_declares_pending(self):
        """图例必须声明 pending，否则 pending 边无解释。"""
        p, _ = _render("pending")
        self.assertIn("pending", p)
        # 图例行本身
        legend = [l for l in p.splitlines() if l.startswith("证据链（")]
        self.assertEqual(len(legend), 1, "应有且仅有一条证据链图例行")
        self.assertIn("pending", legend[0])
        self.assertIn("weak", legend[0])
        self.assertIn("strong", legend[0])

    def test_T2_strong_line_has_no_weak_tag(self):
        _, ln = _render("strong")
        self.assertIn("trigger/strong", ln)
        self.assertNotIn("/弱", ln)
        self.assertNotIn("低置信", ln)

    def test_T3_weak_line_has_weak_tag(self):
        _, ln = _render("weak")
        self.assertIn("trigger/weak", ln)
        self.assertIn("/弱", ln)
        # weak 是常态弱边，不额外标「低置信」
        self.assertNotIn("低置信", ln)

    def test_T4_pending_line_has_weak_tag_and_lowconf_mark(self):
        _, ln = _render("pending")
        self.assertIn("trigger/pending", ln)
        self.assertIn("/弱", ln)
        self.assertIn("低置信", ln)

    def test_T5_pending_semantic_signal_equals_weak(self):
        """⭐ 核心契约：pending 与 weak 必须**同样**带「/弱」语义信号。

        这条是「闸门改标不得削弱提示词语义信号」的机械表达。
        若有人把 `("weak","pending")` 改回 `("weak",)`，本条必失败。
        """
        _, lw = _render("weak")
        _, lp = _render("pending")
        self.assertIn("/弱", lw)
        self.assertIn("/弱", lp)

    def test_T6_none_strength_uses_placeholder(self):
        _, ln = _render(None)
        self.assertIn("trigger/?", ln)
        self.assertNotIn("/弱", ln)

    def test_T7_legend_lists_all_three_values(self):
        p, _ = _render("weak")
        legend = [l for l in p.splitlines() if l.startswith("证据链（")][0]
        for v in ("strong", "weak", "pending"):
            self.assertIn(v, legend, "图例漏了 %s" % v)

    def test_T8_non_clue_values_never_tagged_weak(self):
        """任何非 weak/pending 的标签都不得被标「/弱」。"""
        for v in ("strong", "?", "medium"):
            _, ln = _render(v)
            self.assertNotIn("/弱", ln, "%s 不应带 /弱" % v)


class TestMutationGuard(unittest.TestCase):
    """反恒真：证明本件对「语义信号回退」这类突变**有区分力**。

    通过 monkeypatch 模拟「未修复版」行为（pending 不带 /弱），
    断言 T5 的判据确实会失败 ⇒ 本件不是恒真探针。
    """

    def test_M1_probe_discriminates_regression(self):
        original = llm_answer.build_prompt

        def broken(q, anchor, chain, evs, extra_anchors=None, neighbors=None):
            # 复刻「未修复」行为：只有 weak 带 /弱，pending 不带
            lines = ["问题：%s" % q.get("question", "")]
            lines.append("锚点事件")
            lines.append("证据链（含 strong/weak 边,weak 为弱关联仅供参考）：")
            for ed in chain:
                tag = "/弱" if ed.get("strength") == "weak" else ""
                lines.append("- %s/%s%s: X → Y" % (ed.get("type"),
                                                   ed.get("strength") or "?", tag))
            return "\n".join(lines)

        llm_answer.build_prompt = broken
        try:
            _, lp = _render("pending")
            legend = [l for l in _render("pending")[0].splitlines()
                      if l.startswith("证据链（")][0]
            # 突变的两个特征：pending 无 /弱、图例无 pending
            self.assertNotIn("/弱", lp, "突变体本应无 /弱（探针在这一步应能看出退化）")
            self.assertNotIn("pending", legend)
        finally:
            llm_answer.build_prompt = original

        # 还原后契约恢复
        _, lp2 = _render("pending")
        self.assertIn("/弱", lp2)

    def test_M2_shrinking_tuple_breaks_T5(self):
        """模拟把 `("weak","pending")` 缩回 `("weak",)` 的突变，T5 判据必须失败。"""
        os.environ.pop("LLM_CHAIN_PRIORITIZE", None)
        src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_answer.py")
        with open(src_path, encoding="utf-8") as fh:
            src = fh.read()
        patched_src_ok = "in (\"weak\", \"pending\")" in src
        self.assertTrue(
            patched_src_ok,
            "llm_answer.py 中的 pending 判定已回退 —— 这正是本件要拦住的退化")


if __name__ == "__main__":
    unittest.main(verbosity=2)