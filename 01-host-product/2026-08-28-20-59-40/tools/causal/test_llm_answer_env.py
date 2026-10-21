# -*- coding: utf-8 -*-
"""`LLM_*` 门控环境变量的**三态**回归件；无凭证、无网络、不碰生产图谱。

## 为什么需要这个件

原实现把门控写成裸真值 / `bool(os.environ.get(...))`：

    if os.environ.get("LLM_FALLBACK") and not anchors:      # 旧
    rev_ok = bool(os.environ.get("LLM_REV_EDGES"))          # 旧

`bool("0") == True` ⇒ **显式把开关置 0 反而会「开启」它**。本项目已两次踩到同一坑
（`LLM_REV_EDGES=0`、`LLM_FALLBACK=0`）⇒ 复跑口径不可复现。

## 本件锁定的契约

| 取值 | `env_flag(name, default=False)` |
|---|---|
| 未设置 | `default` |
| `""` / 纯空白 | `default` |
| `0` / `false` / `no` / `off`（大小写、含首尾空白） | `False` |
| 其余非空 | `True` |

## 反恒真自证

1. `TamperTests` 直接重放**旧实现**（`bool(os.environ.get(...))`），断言它在 `"0"` 上与
   新实现**结论相反** —— 否则这些用例分辨不出回归。
2. `ShapeTests` 用正则扫描源码，禁止裸真值门控**再次出现**；该正则已对
   **修复前**的版本实测命中 4 处（3 处裸真值 + 1 处 `bool(...)`），对本版命中 0。
"""

import io
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import llm_answer


def read_source():
    with io.open(str(HERE / "llm_answer.py"), encoding="utf-8") as f:
        return f.read()


class _EnvSandbox(unittest.TestCase):
    """三态环境变量夹具：未设置 / 空串 / 显式值。"""

    NAMES = ("LLM_FALLBACK", "LLM_NEIGHBORS", "LLM_REV_EDGES", "LLM_PROMPT_CAUSE")

    def setUp(self):
        self._saved = {n: os.environ.get(n) for n in self.NAMES}
        for n in self.NAMES:
            os.environ.pop(n, None)
        self.addCleanup(self._restore)

    def _restore(self):
        for n, v in self._saved.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v

    def set_env(self, name, value):
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


class EnvFlagUnitTests(_EnvSandbox):
    """`env_flag` 本身的真值表。"""

    def test_unset_returns_default(self):
        self.assertFalse(llm_answer.env_flag("LLM_FALLBACK"))
        self.assertTrue(llm_answer.env_flag("LLM_FALLBACK", default=True))

    def test_empty_and_blank_return_default(self):
        for raw in ("", "   "):
            self.set_env("LLM_FALLBACK", raw)
            self.assertFalse(llm_answer.env_flag("LLM_FALLBACK"),
                             "raw=%r 应回落 default" % raw)
            self.assertTrue(llm_answer.env_flag("LLM_FALLBACK", default=True))

    def test_falsy_words_are_false(self):
        for raw in ("0", "false", "FALSE", "no", "off", " 0 ", "Off"):
            self.set_env("LLM_FALLBACK", raw)
            self.assertFalse(llm_answer.env_flag("LLM_FALLBACK"),
                             "raw=%r 必须是 False（旧实现在此处判 True）" % raw)

    def test_truthy_values_are_true(self):
        for raw in ("1", "true", "yes", "on", "x", " 1 "):
            self.set_env("LLM_FALLBACK", raw)
            self.assertTrue(llm_answer.env_flag("LLM_FALLBACK"), "raw=%r 应为 True" % raw)


class TamperTests(_EnvSandbox):
    """**核心**：重放旧实现，断言新旧在 `"0"` 上结论相反（否则用例是恒真探针）。"""

    @staticmethod
    def legacy(raw):
        """修复前的写法。"""
        return bool(raw)

    def test_legacy_disagrees_with_new_on_zero(self):
        raw = "0"
        self.assertTrue(self.legacy(raw), "旧实现：bool('0') 为 True")
        self.set_env("LLM_FALLBACK", raw)
        self.assertFalse(llm_answer.env_flag("LLM_FALLBACK"),
                         "新实现：'0' 必须为 False")
        self.assertNotEqual(self.legacy(raw), llm_answer.env_flag("LLM_FALLBACK"),
                            "新旧在 '0' 上必须相反 —— 否则本件分辨不出回归")

    def test_new_matches_legacy_when_unset_or_one(self):
        """修复**不得**改变历史上真正用过的两种取值（未设置 / =1）。"""
        for raw in (None, "1"):
            self.set_env("LLM_FALLBACK", raw)
            self.assertEqual(self.legacy(raw), llm_answer.env_flag("LLM_FALLBACK"),
                             "raw=%r 下新旧必须一致 ⇒ 历史复跑不受影响" % raw)


class PrepareQuestionThreeStateTests(_EnvSandbox):
    """端到端：`prepare_question` 的兜底门控在三态下行为必须各异。"""

    ANCHOR = {"id": "A1", "ts": "2026-01-01", "type": "t", "subject": "s",
              "action": "a", "object": "o"}
    FB = [{"id": "FB1"}, {"id": "FB2"}, {"id": "FB3"}]
    NEIGHBORS = [(0, {"id": "N%d" % i}) for i in range(1, 6)]

    def _run(self, fallback_anchors, neighbors=None):
        """把检索层换成确定性替身；返回 prepare_question 的输出。"""
        with mock.patch.object(llm_answer, "extract_keywords_v2", lambda q: []), \
             mock.patch.object(llm_answer, "extract_keywords", lambda q: []), \
             mock.patch.object(llm_answer, "retrieve_top",
                               lambda *a, **k: list(fallback_anchors)), \
             mock.patch.object(llm_answer, "retrieve_hybrid", lambda *a, **k: []), \
             mock.patch.object(llm_answer, "retrieve_fallback",
                               lambda events, q, k=3: list(self.FB)), \
             mock.patch.object(llm_answer, "chain_recall",
                               lambda *a, **k: {"edges": []}), \
             mock.patch.object(llm_answer, "temporal_neighbors",
                               lambda *a, **k: list(neighbors or [])):
            return llm_answer.prepare_question("unused.db", {"question": "q"}, [])

    def test_fallback_off_when_unset(self):
        out = self._run([])
        self.assertEqual(out["fallback_used"], 0)
        self.assertEqual(out["anchors"], [])

    def test_fallback_off_when_empty(self):
        self.set_env("LLM_FALLBACK", "")
        self.assertEqual(self._run([])["fallback_used"], 0)

    def test_fallback_off_when_explicit_zero(self):
        """**这就是原缺陷**：置 0 时必须真的关掉。"""
        self.set_env("LLM_FALLBACK", "0")
        out = self._run([])
        self.assertEqual(out["fallback_used"], 0, "LLM_FALLBACK=0 仍触发兜底 ⇒ 假值陷阱未修")
        self.assertEqual(out["anchors"], [])

    def test_fallback_on_when_one(self):
        self.set_env("LLM_FALLBACK", "1")
        out = self._run([])
        self.assertEqual(out["fallback_used"], len(self.FB))
        self.assertEqual([a["id"] for a in out["anchors"]],
                         [x["id"] for x in self.FB])

    def test_fallback_not_used_when_anchors_present(self):
        """有锚点时兜底本就不该介入（与开关无关）。"""
        self.set_env("LLM_FALLBACK", "1")
        out = self._run([dict(self.ANCHOR)])
        self.assertEqual(out["fallback_used"], 0)

    def test_neighbors_off_when_explicit_zero(self):
        self.set_env("LLM_NEIGHBORS", "0")
        out = self._run([dict(self.ANCHOR)], neighbors=self.NEIGHBORS)
        self.assertEqual(out["neighbors"], [], "LLM_NEIGHBORS=0 仍取邻居 ⇒ 假值陷阱未修")

    def test_neighbors_on_when_one(self):
        self.set_env("LLM_NEIGHBORS", "1")
        out = self._run([dict(self.ANCHOR)], neighbors=self.NEIGHBORS)
        self.assertEqual(len(out["neighbors"]), 4, "邻居上限为 4")

    def test_neighbors_absent_when_unset(self):
        out = self._run([dict(self.ANCHOR)], neighbors=self.NEIGHBORS)
        self.assertEqual(out["neighbors"], [])


class ShapeTests(unittest.TestCase):
    """禁止裸真值门控**再次出现**（对修复前版本实测命中 4 处）。"""

    BARE_GATE = re.compile(r'(?:if|and|or|not)\s+os\.environ\.get\("[A-Z_]+"\)\s*(?=[:\n]|and\b|or\b)')
    BOOL_WRAP = re.compile(r'bool\(os\.environ\.get\("[A-Z_]+"\)\)')

    def test_no_bare_truthiness_gate_remains(self):
        src = read_source()
        self.assertEqual(self.BARE_GATE.findall(src), [],
                         "出现裸真值门控 ⇒ '0' 会被判为开启")
        self.assertEqual(self.BOOL_WRAP.findall(src), [],
                         "出现 bool(os.environ.get(...)) ⇒ '0' 会被判为开启")

    def test_the_four_gates_go_through_env_flag(self):
        src = read_source()
        for name in ("LLM_REV_EDGES", "LLM_PROMPT_CAUSE", "LLM_FALLBACK", "LLM_NEIGHBORS"):
            self.assertIn('env_flag("%s")' % name, src,
                          "%s 未走 env_flag ⇒ 三态语义无保证" % name)

    def test_detector_rejects_the_legacy_shape(self):
        """检测器自身必须能拒绝旧写法，否则它是装饰品。"""
        legacy = ('    if os.environ.get("LLM_FALLBACK") and not anchors:\n'
                  '        pass\n'
                  '    rev_ok = bool(os.environ.get("LLM_REV_EDGES"))\n')
        self.assertEqual(len(self.BARE_GATE.findall(legacy)), 1)
        self.assertEqual(len(self.BOOL_WRAP.findall(legacy)), 1)
        self.assertEqual(self.BARE_GATE.findall(read_source()), [],
                         "本版源码不得命中")


if __name__ == "__main__":
    unittest.main(verbosity=2)
