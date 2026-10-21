#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""逐题 rationale 留存的离线回归件（P1-4，2026-09-29）。

对应验收标准（`docs/Gate3-进度与优先级规划-20260928.md` §P1-4）：

  ① 新旧产物 diff **只增** `rationale` 字段（其余字段逐字节不变）
     → `test_product_diff_is_only_rationale`：**同进程 A/B** —— 把 `parse_json` 回退为
       「丢弃 rationale」的旧行为，用**同一份录制响应**再跑一遍 `main()`，逐字段比对。
       判据强于「键集合差」：连 `input_fingerprint` / `metrics` / `meta` 都比，只放行 `generated`。
  ② 回归件**对未修复版实测 FAIL**（非恒真探针，红线 10）
     → `--self-test`：子进程 + `JVS_LEGACY_JUDGE_EMULATION=1` **精确复刻**未修复语义
       （旧 `parse_json` 的实现就是 `{k: d[k] for k in JUDGE_FIELDS}`，故「丢字段」= 旧行为本身），
       要求未修复版**必须** FAIL 且失败数 ≥ 3（单点命中不算，要成片命中）。
  ③ 已挂门禁 → `scripts/git-gate.sh` [3] 段。

隔离口径与 `test_llm_judge.py` 一致：不调模型、不读凭证、不碰冻结产物与生产库。

用法:
  python -B test_llm_judge_rationale.py              # 正常跑（门禁走这条）
  python -B test_llm_judge_rationale.py --self-test  # 负向自证：未修复版必须 FAIL

环境:
  JVS_JUDGE_MODULE_PATH=<path>     指定被测判分器（默认同目录 llm_judge.py）
  JVS_LEGACY_JUDGE_EMULATION=1     把 parse_json 回退为「丢弃 rationale」的旧行为
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
MODULE_PATH = os.environ.get("JVS_JUDGE_MODULE_PATH") or str(HERE / "llm_judge.py")
LEGACY_EMULATION = os.environ.get("JVS_LEGACY_JUDGE_EMULATION") == "1"

# 先把本目录放进 sys.path：被测副本可能位于别处（A/B 用的旧版），
# 但它的 `from llm_answer import chat, provider_conf` 仍需在本目录解析。
sys.path.insert(0, str(HERE))
_spec = importlib.util.spec_from_file_location("llm_judge", MODULE_PATH)
judge = importlib.util.module_from_spec(_spec)
sys.modules["llm_judge"] = judge
_spec.loader.exec_module(judge)

FIELDS = tuple(judge.JUDGE_FIELDS)
RATIONALE_FIELD = getattr(judge, "RATIONALE_FIELD", "rationale")
RATIONALE_MAX_CHARS = getattr(judge, "RATIONALE_MAX_CHARS", 600)
HAS_RATIONALE_API = hasattr(judge, "RATIONALE_FIELD") and hasattr(judge, "normalize_rationale")

if LEGACY_EMULATION:
    # 精确复刻未修复版语义：旧 parse_json 末尾是 `return {k: d[k] for k in JUDGE_FIELDS}`，
    # 即「丢掉 4 个布尔字段以外的一切」。此处按同一语义包装，不做任何额外削弱。
    _real_parse_json = judge.parse_json

    def _legacy_parse_json(text, with_rationale=False):
        return {k: v for k, v in _real_parse_json(text).items() if k in judge.JUDGE_FIELDS}

    judge.parse_json = _legacy_parse_json

RATIONALE_TEXT = "cause 命中标准答案的『alpha』；effect 命中『beta』"
VALID = json.dumps({**dict.fromkeys(FIELDS, True), RATIONALE_FIELD: RATIONALE_TEXT})
NEGATIVE = json.dumps({**dict.fromkeys(FIELDS, False), RATIONALE_FIELD: ""})


class RationaleTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="judge-rationale-")
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.answers = self.root / "answers.jsonl"
        self.gold = self.root / "gold.jsonl"
        self.scores = self.root / "scores.json"
        self.checkpoint = Path(str(self.scores) + ".checkpoint.jsonl")
        self.conf = {"provider": "sensenova", "model": "test-model",
                     "url": "https://example.invalid/test", "key": "synthetic-secret"}
        self.write_rows(self.answers, [self.answer("Q001")])
        self.write_rows(self.gold, [self.reference("Q001")])
        for target, kwargs in (
            ("urllib.request.urlopen", {"side_effect": AssertionError("Network prohibited")}),
            ("llm_judge.provider_conf", {"side_effect": lambda: dict(self.conf)}),
            ("llm_judge.time.sleep", {}),
        ):
            p = patch(target, **kwargs)
            p.start()
            self.addCleanup(p.stop)
        env = patch.dict(os.environ, {"LLM_JUDGE_CAUSE_RELAX": "0", "LLM_JUDGE_MAX_TOKENS": "4096"})
        env.start()
        self.addCleanup(env.stop)

    # ---------- 夹具 ----------
    @staticmethod
    def answer(qid):
        return {"qid": qid, "question": "synthetic question",
                "answer_text": "synthetic cause and effect", "pipeline_ok": True,
                "latency_ms": 40000}

    @staticmethod
    def reference(qid):
        return {"qid": qid, "question": "synthetic question",
                "cause": "alpha", "effect": "beta", "evidence_hint": "gamma"}

    @staticmethod
    def write_rows(path, rows):
        Path(path).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def run_main(self, response=VALID, out=None, extra=()):
        out = Path(out) if out else self.scores
        argv = ["llm_judge.py", "--answers", str(self.answers), "--gold", str(self.gold),
                "--out", str(out), *extra]
        with patch.object(sys, "argv", argv), \
                patch.object(judge, "chat", return_value=response) as model, \
                contextlib.redirect_stdout(io.StringIO()):
            judge.main()
        return json.loads(Path(out).read_text(encoding="utf-8")), model.call_count

    # ---------- 判分器本体：rationale 契约 ----------
    def test_self_check_wiring(self):
        """被测件必须真的是「已修复版」——否则本文件的其它断言没有意义。"""
        self.assertTrue(HAS_RATIONALE_API,
                        f"被测判分器 {MODULE_PATH} 缺 rationale API（未修复版？）")

    def test_rationale_retained(self):
        row = judge.parse_json(VALID, with_rationale=True)
        self.assertEqual(row[RATIONALE_FIELD], RATIONALE_TEXT)
        self.assertTrue(all(row[k] is True for k in FIELDS))

    def test_default_parse_contract_unchanged(self):
        """默认调用（既有调用方走这条）必须仍只返回 4 个布尔字段。"""
        for raw in (VALID, NEGATIVE, "```json\n" + VALID + "\n```"):
            with self.subTest(raw=raw[:24]):
                self.assertEqual(set(judge.parse_json(raw).keys()), set(FIELDS))
        self.assertTrue(all(judge.parse_json(VALID).values()))
        self.assertFalse(any(judge.parse_json(NEGATIVE).values()))

    def test_missing_rationale_is_empty(self):
        for payload in (dict.fromkeys(FIELDS, True), {**dict.fromkeys(FIELDS, True),
                                                      RATIONALE_FIELD: None}):
            with self.subTest(payload=str(payload)[:24]):
                row = judge.parse_json(json.dumps(payload), with_rationale=True)
                self.assertEqual(row[RATIONALE_FIELD], "")

    def test_nonstring_rationale_rejected(self):
        for bad in (123, 1.5, True, {}, [], {"text": "x"}):
            with self.subTest(bad=repr(bad)):
                raw = json.dumps({**dict.fromkeys(FIELDS, True), RATIONALE_FIELD: bad})
                with self.assertRaises(judge.JudgeResponseError) as caught:
                    judge.parse_json(raw, with_rationale=True)
                self.assertEqual(caught.exception.error_type, "response_schema_invalid")

    def test_rationale_is_trimmed_and_capped(self):
        raw = json.dumps({**dict.fromkeys(FIELDS, True),
                          RATIONALE_FIELD: "  " + "x" * (RATIONALE_MAX_CHARS + 50) + "  "})
        row = judge.parse_json(raw, with_rationale=True)
        self.assertEqual(len(row[RATIONALE_FIELD]), RATIONALE_MAX_CHARS)
        self.assertEqual(row[RATIONALE_FIELD], "x" * RATIONALE_MAX_CHARS)

    def test_failure_row_has_empty_rationale(self):
        for error_type in ("api_429", "response_invalid_json", "answer_missing"):
            with self.subTest(error_type=error_type):
                row = judge._judge_failure(error_type)
                self.assertEqual(row[RATIONALE_FIELD], "")
                self.assertFalse(row["judge_ok"])

    # ---------- 产物层：只增 rationale ----------
    def test_product_item_carries_rationale(self):
        data, calls = self.run_main()
        self.assertEqual(calls, 1)
        self.assertEqual(data["items"][0][RATIONALE_FIELD], RATIONALE_TEXT)
        self.assertTrue(data["items"][0]["judge_ok"])

    def test_failure_product_item_carries_empty_rationale(self):
        data, _ = self.run_main(response="")
        self.assertFalse(data["items"][0]["judge_ok"])
        self.assertEqual(data["items"][0][RATIONALE_FIELD], "")

    def test_rationale_not_in_metrics_or_meta(self):
        """「只增」的边界：rationale 不得渗进指标或元数据。"""
        data, _ = self.run_main()
        for block in ("metrics", "metric_counts", "error_counts"):
            self.assertNotIn(RATIONALE_FIELD, json.dumps(data[block], ensure_ascii=False))
        self.assertEqual(set(data["meta"]),
                         {"generated", "answers", "gold", "judge_config", "endpoint_sha256",
                          "parse_retries", "judge_attempts_scope", "score_timeout_ms",
                          "availability_scope", "generation_success_scope"})
        self.assertNotIn(RATIONALE_FIELD, data["meta"]["judge_config"])
        self.assertEqual(set(data), {"meta", "metrics", "metric_counts", "error_counts", "items"})

    def test_product_diff_is_only_rationale(self):
        """验收标准 ① 的机械兑现：同进程 A/B（旧行为 vs 新行为，同一份录制响应）。"""
        new_out, old_out = self.root / "new.json", self.root / "old.json"
        new, _ = self.run_main(response=VALID, out=new_out)
        original = judge.parse_json

        def legacy(text, with_rationale=False):
            return {k: v for k, v in original(text).items() if k in FIELDS}

        judge.parse_json = legacy
        try:
            old, _ = self.run_main(response=VALID, out=old_out)
        finally:
            judge.parse_json = original

        new_item, old_item = new["items"][0], old["items"][0]
        self.assertEqual(set(new_item) - set(old_item), {RATIONALE_FIELD})
        self.assertEqual(set(old_item) - set(new_item), set())
        for key in old_item:
            self.assertEqual(new_item[key], old_item[key], f"字段 {key} 发生了变化")
        # 关键：解析层改动**不应**触碰指纹（提示词未变），故连指纹都必须逐字节相同。
        self.assertEqual(new_item["input_fingerprint"], old_item["input_fingerprint"])
        for block in ("metrics", "metric_counts", "error_counts"):
            self.assertEqual(new[block], old[block], f"{block} 发生了变化")
        self.assertEqual({k: v for k, v in new["meta"].items() if k != "generated"},
                         {k: v for k, v in old["meta"].items() if k != "generated"},
                         "meta 发生了变化（只允许 generated 时间戳不同）")

    # ---------- 断点层 ----------
    def test_checkpoint_preserves_rationale(self):
        first, calls = self.run_main()
        second, reused = self.run_main()
        self.assertEqual((calls, reused), (1, 0))
        self.assertEqual(second["items"], first["items"])
        self.assertEqual(second["items"][0][RATIONALE_FIELD], RATIONALE_TEXT)

    def test_checkpoint_rejects_nonstring_rationale(self):
        data, _ = self.run_main()
        row = data["items"][0]
        expected = {row["qid"]: row["input_fingerprint"]}
        for bad in ({**row, RATIONALE_FIELD: 7}, {**row, RATIONALE_FIELD: ["x"]}):
            self.write_rows(self.checkpoint, [bad])
            self.assertEqual(judge._load_checkpoint(str(self.checkpoint), expected), {})

    def test_checkpoint_absent_rationale_is_tolerated(self):
        """旧断点（无该字段）不得因此整批判分失败 —— 只补空串，不改变判定。"""
        data, _ = self.run_main()
        row = {k: v for k, v in data["items"][0].items() if k != RATIONALE_FIELD}
        expected = {row["qid"]: row["input_fingerprint"]}
        self.write_rows(self.checkpoint, [row])
        loaded = judge._load_checkpoint(str(self.checkpoint), expected)
        self.assertEqual(set(loaded), {row["qid"]})
        self.assertEqual(loaded[row["qid"]][RATIONALE_FIELD], "")


# ============================ 负向自证 ============================

def _run_child(extra_env):
    env = dict(os.environ)
    env.pop("JVS_LEGACY_JUDGE_EMULATION", None)
    env.update(extra_env)
    proc = subprocess.run([sys.executable, "-B", os.path.abspath(__file__)],
                          cwd=str(HERE), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _failures(output):
    line = [ln for ln in output.splitlines() if ln.startswith("FAILED (")]
    if not line:
        return 0
    try:
        return int(line[0].split("failures=")[1].split(")")[0].split(",")[0])
    except (IndexError, ValueError):
        return -1


def self_test():
    print("=" * 78)
    print(" test_llm_judge_rationale.py --self-test  （判据：能拒绝才算校验，红线 10）")
    print("=" * 78)
    rc_fixed, out_fixed = _run_child({})
    rc_legacy, out_legacy = _run_child({"JVS_LEGACY_JUDGE_EMULATION": "1"})
    n_fixed, n_legacy = _failures(out_fixed), _failures(out_legacy)
    print(f"  [已修复版] rc={rc_fixed} 失败数={n_fixed}（期望 rc=0 / 失败 0）")
    print(f"  [未修复版] rc={rc_legacy} 失败数={n_legacy}（期望 rc!=0 / 失败 >=3）")
    checks = [
        ("已修复版全过", rc_fixed == 0 and n_fixed == 0),
        ("未修复版实测 FAIL", rc_legacy != 0),
        ("未修复版成片命中（>=3 项）", n_legacy >= 3),
    ]
    ok = all(flag for _, flag in checks)
    for label, flag in checks:
        print(f"  {'[PASS]' if flag else '[FAIL]'} {label}")
    if not ok:
        print("\n--- 未修复版输出尾部 ---")
        print("\n".join(out_legacy.splitlines()[-25:]))
    print("=" * 78)
    print(" 自证结论:", "非恒真探针（未修复版被拒绝）" if ok else "自证失败 —— 判据不可信")
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        raise SystemExit(self_test())
    unittest.main(verbosity=2)
