"""Offline judge regressions: no model, credentials, frozen data, or production DB access."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import llm_judge as judge

FIELDS = judge.JUDGE_FIELDS
VALID = json.dumps(dict.fromkeys(FIELDS, True))
NEGATIVE = json.dumps(dict.fromkeys(FIELDS, False))


class JudgeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="judge-test-")
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.answers = self.root / "answers.jsonl"
        self.gold = self.root / "gold.jsonl"
        self.scores = self.root / "scores.json"
        self.checkpoint = Path(str(self.scores) + ".checkpoint.jsonl")
        self.conf = {"provider": "sensenova", "model": "test-model", "url": "https://example.invalid/test", "key": "synthetic-secret"}
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

    @staticmethod
    def answer(qid):
        return {"qid": qid, "question": "synthetic question", "answer_text": "synthetic cause and effect", "pipeline_ok": True, "latency_ms": 40000}

    @staticmethod
    def reference(qid):
        return {"qid": qid, "question": "synthetic question", "cause": "alpha", "effect": "beta", "evidence_hint": "gamma"}

    @staticmethod
    def write_rows(path, rows):
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def run_main(self, response=VALID, effects=None, extra=()):
        argv = ["llm_judge.py", "--answers", str(self.answers), "--gold", str(self.gold), "--out", str(self.scores), *extra]
        with patch.object(sys, "argv", argv), patch.object(judge, "chat", return_value=response, side_effect=effects) as model, contextlib.redirect_stdout(io.StringIO()):
            judge.main()
        return json.loads(self.scores.read_text(encoding="utf-8")), model.call_count

    def test_bool_and_fenced_object(self):
        self.assertFalse(any(judge.parse_json(NEGATIVE).values()))
        self.assertTrue(all(judge.parse_json("```json\n" + VALID + "\n```").values()))

    def test_empty_responses(self):
        for raw in (None, "", " \n\t"):
            with self.subTest(raw=raw), self.assertRaises(judge.JudgeResponseError) as caught:
                judge.parse_json(raw)
            self.assertEqual(caught.exception.error_type, "response_empty")

    def test_invalid_json(self):
        for raw in ("not JSON", "{broken}", VALID + " garbage", "{" + VALID):
            with self.subTest(raw=raw), self.assertRaises(judge.JudgeResponseError) as caught:
                judge.parse_json(raw)
            self.assertEqual(caught.exception.error_type, "response_invalid_json")

    def test_invalid_schema(self):
        for raw in ("[]", "null", "true", "42", "{}", [1], json.dumps({"nested": json.loads(VALID)}), json.dumps(dict.fromkeys(FIELDS, "false")), json.dumps(dict.fromkeys(FIELDS, 1))):
            with self.subTest(raw=raw), self.assertRaises(judge.JudgeResponseError) as caught:
                judge.parse_json(raw)
            self.assertEqual(caught.exception.error_type, "response_schema_invalid")

    def test_fence_cannot_salvage_inner_object(self):
        cases = [
            ("```json\n[" + VALID + "]\n```", "response_schema_invalid"),
            ("```json\n" + VALID + ",\n```", "response_invalid_json"),
            ("```json\n" + VALID + " garbage\n```", "response_invalid_json"),
            ("commentary " + VALID, "response_invalid_json"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw), self.assertRaises(judge.JudgeResponseError) as caught:
                judge.parse_json(raw)
            self.assertEqual(caught.exception.error_type, expected)

    def test_duplicate_keys_rejected(self):
        raw = VALID.replace('"cause": true', '"cause": false, "cause": true')
        with self.assertRaises(judge.JudgeResponseError) as caught:
            judge.parse_json(raw)
        self.assertEqual(caught.exception.error_type, "response_invalid_json")

    def test_decoder_limits_keep_batch_running(self):
        limit = sys.get_int_max_str_digits()
        long_number = '{"cause":' + "9" * (max(limit, 4300) + 1) + "}"
        deep = "[" * (sys.getrecursionlimit() + 100) + "]" * (sys.getrecursionlimit() + 100)
        for malformed in (long_number, deep):
            with self.subTest(kind="integer" if malformed == long_number else "depth"):
                self.write_rows(self.gold, [self.reference(q) for q in ("Q001", "Q002")])
                self.write_rows(self.answers, [self.answer(q) for q in ("Q001", "Q002")])
                self.scores.unlink(missing_ok=True)
                self.checkpoint.unlink(missing_ok=True)
                if malformed == deep:
                    real_loads = json.loads
                    def decode(raw, *args, **kwargs):
                        if raw == deep:
                            raise RecursionError("synthetic decoder recursion limit")
                        return real_loads(raw, *args, **kwargs)
                    with patch.object(judge.json, "loads", side_effect=decode):
                        data, calls = self.run_main(effects=[malformed, malformed, VALID])
                else:
                    data, calls = self.run_main(effects=[malformed, malformed, VALID])
                self.assertEqual(calls, 3)
                self.assertEqual(data["metrics"]["n"], 2)
                self.assertEqual(data["items"][0]["error_type"], "response_invalid_json")
                self.assertTrue(data["items"][1]["judge_ok"])
                self.assertEqual(len(judge._load_rows(str(self.checkpoint))), 2)

    def test_transport_classification(self):
        cases = [
            (RuntimeError("HTTP 429: quota"), "api_429"),
            (RuntimeError("HTTP 503: no json in: credentials"), "api_5xx"),
            (RuntimeError("HTTP 401: Forbidden"), "api_4xx"),
            (urllib.error.HTTPError("https://example.invalid", 502, "bad gateway", {}, None), "api_5xx"),
            (TimeoutError(), "api_timeout/network"),
            (urllib.error.URLError("DNS failure"), "api_timeout/network"),
            (RuntimeError("TimeoutError('timed out')"), "api_timeout/network"),
            (RuntimeError("RemoteDisconnected('closed')"), "api_timeout/network"),
            (RuntimeError("JSONDecodeError('Expecting value')"), "response_invalid_json"),
            (RuntimeError("KeyError('choices')"), "response_schema_invalid"),
            (RuntimeError("AttributeError('NoneType strip')"), "response_schema_invalid"),
            (ValueError("no json in: "), "response_empty"),
            (ValueError("judge fields must be JSON booleans: cause"), "response_schema_invalid"),
            (RuntimeError("unexpected"), "api_unknown"),
        ]
        for exc, expected in cases:
            with self.subTest(exc=repr(exc)):
                self.assertEqual(judge.classify_judge_error(exc), expected)

    def test_error_diagnostics_never_persist_payload(self):
        for exc in (RuntimeError('HTTP 401: {"api_key":"synthetic-secret"} Bearer opaque-token'), ValueError("no json in: private-answer-text"), RuntimeError("unstructured-secret")):
            text = judge._safe_error_text(exc)
            for private in ("synthetic-secret", "opaque-token", "private-answer-text", "unstructured-secret"):
                self.assertNotIn(private, text)

    def test_empty_retry_then_success(self):
        with patch.object(judge, "chat", side_effect=["", VALID]) as model:
            result = judge.judge_answer("fixed prompt", 4096)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(model.call_args_list[0], model.call_args_list[1])
        self.assertTrue(result["judge_ok"])
        self.assertEqual(result["error_type"], "")
        self.assertEqual(result["attempt_errors"], ["response_empty"])
        self.assertEqual(result["parse_retry_count"], 1)

    def test_non_json_retry_bound(self):
        with patch.object(judge, "chat", return_value="not JSON") as model:
            result = judge.judge_answer("p", 4096)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(result["error_type"], "response_invalid_json")
        self.assertFalse(result["judge_ok"])
        self.assertEqual(result["judge_attempts"], 2)
        self.assertNotIn("语义不可用", result["unavailable_reason"])

    def test_schema_not_retried(self):
        with patch.object(judge, "chat", return_value='{"cause":"false"}') as model:
            result = judge.judge_answer("p", 4096)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(result["error_type"], "response_schema_invalid")

    def test_transport_not_retried_by_judge(self):
        for exc in (RuntimeError("HTTP 429: limit"), RuntimeError("HTTP 503: failure"), TimeoutError("timed out"), RuntimeError("JSONDecodeError('invalid HTTP envelope')")):
            with self.subTest(exc=repr(exc)), patch.object(judge, "chat", side_effect=exc) as model:
                result = judge.judge_answer("p", 4096)
                self.assertEqual(model.call_count, 1)
                self.assertFalse(result["judge_ok"])

    def test_retry_disabled(self):
        data, calls = self.run_main(response="", extra=("--parse-retries", "0"))
        self.assertEqual(calls, 1)
        self.assertEqual(data["items"][0]["parse_retry_count"], 0)
        with self.assertRaises(ValueError):
            judge.judge_answer("p", 4096, 2)

    def test_semantic_unavailable_is_success(self):
        data, calls = self.run_main(response=NEGATIVE)
        row = data["items"][0]
        self.assertEqual(calls, 1)
        self.assertTrue(row["judge_ok"])
        self.assertFalse(row["available"])
        self.assertEqual(row["error_type"], "")
        self.assertIn("语义不可用", row["unavailable_reason"])
        self.assertEqual(data["metrics"]["judge_success_rate"], 1)
        self.assertEqual(data["metrics"]["availability"], 0)
        _, calls = self.run_main()
        self.assertEqual(calls, 0)

    def test_successful_checkpoint_reused(self):
        first, calls = self.run_main()
        second, reused_calls = self.run_main()
        self.assertEqual((calls, reused_calls), (1, 0))
        self.assertEqual(first["items"], second["items"])
        self.assertEqual(len(second["items"]), 1)

    def test_failed_checkpoint_rejudged(self):
        failed, first_calls = self.run_main(response="")
        passed, second_calls = self.run_main()
        self.assertEqual((first_calls, second_calls), (2, 1))
        self.assertFalse(failed["items"][0]["judge_ok"])
        self.assertTrue(passed["items"][0]["judge_ok"])
        self.assertEqual(failed["metrics"]["n"], passed["metrics"]["n"])

    def test_checkpoint_rejects_nonboolean_and_error_rows(self):
        data, _ = self.run_main()
        row = data["items"][0]
        expected = {row["qid"]: row["input_fingerprint"]}
        for bad in ({**row, "judge_ok": "true"}, {**row, "cause": "false"}, {**row, "error_type": "api_429"}):
            self.write_rows(self.checkpoint, [bad])
            self.assertEqual(judge._load_checkpoint(str(self.checkpoint), expected), {})

    def test_answer_and_gold_changes_invalidate(self):
        self.run_main()
        self.write_rows(self.answers, [{**self.answer("Q001"), "answer_text": "changed"}])
        _, calls = self.run_main()
        self.assertEqual(calls, 1)
        self.write_rows(self.gold, [{**self.reference("Q001"), "cause": "changed"}])
        _, calls = self.run_main()
        self.assertEqual(calls, 1)

    def test_effective_model_and_endpoint_changes_invalidate(self):
        self.run_main()
        self.conf["model"] = "different-model"
        _, calls = self.run_main()
        self.assertEqual(calls, 1)
        self.conf["url"] = "https://different.invalid/test"
        _, calls = self.run_main()
        self.assertEqual(calls, 1)

    def test_latency_refresh_does_not_repeat_semantics(self):
        self.run_main()
        self.write_rows(self.answers, [{**self.answer("Q001"), "latency_ms": 50000}])
        data, calls = self.run_main()
        self.assertEqual(calls, 0)
        self.assertEqual(data["items"][0]["latency_ms"], 50000)

    def test_missing_and_failed_answers_keep_gold_denominator(self):
        self.write_rows(self.gold, [self.reference(q) for q in ("Q001", "Q002", "Q003")])
        self.write_rows(self.answers, [self.answer("Q001"), {**self.answer("Q002"), "pipeline_ok": False}])
        data, calls = self.run_main()
        self.assertEqual(calls, 1)
        self.assertEqual(data["metrics"]["n"], 3)
        self.assertEqual(data["metric_counts"]["judge_success_rate"], {"numerator": 1, "denominator": 3})
        self.assertEqual(data["error_counts"], {"answer_failure": 1, "answer_missing": 1})
        self.assertEqual(data["items"][2]["judge_attempts"], 0)

    def test_input_state_invalidation(self):
        self.run_main()
        self.write_rows(self.answers, [{**self.answer("Q001"), "pipeline_ok": "false"}])
        data, calls = self.run_main()
        self.assertEqual(calls, 0)
        self.assertFalse(data["items"][0]["judge_ok"])
        self.assertEqual(data["items"][0]["error_type"], "answer_failure")

    def test_invalid_answer_and_legacy_missing_flag(self):
        self.write_rows(self.answers, [{**self.answer("Q001"), "answer_text": 123}])
        data, calls = self.run_main()
        self.assertEqual(calls, 0)
        self.assertEqual(data["items"][0]["error_type"], "answer_invalid")
        legacy = self.answer("Q001")
        legacy.pop("pipeline_ok")
        self.write_rows(self.answers, [legacy])
        data, calls = self.run_main()
        self.assertEqual(calls, 1)
        self.assertTrue(data["items"][0]["judge_ok"])

    def test_later_success_retained_during_interruption(self):
        self.write_rows(self.gold, [self.reference(q) for q in ("Q001", "Q002", "Q003")])
        self.write_rows(self.answers, [self.answer(q) for q in ("Q001", "Q002", "Q003")])
        data, _ = self.run_main()
        rows = data["items"]
        for row in rows[:2]:
            row["judge_ok"] = False
        self.write_rows(self.checkpoint, rows)
        with self.assertRaises(KeyboardInterrupt):
            self.run_main(effects=[VALID, KeyboardInterrupt()])
        checkpoint = judge._load_rows(str(self.checkpoint))
        self.assertEqual({r["qid"] for r in checkpoint}, {"Q001", "Q003"})
        finished, calls = self.run_main()
        self.assertEqual(calls, 1)
        self.assertEqual(finished["metrics"]["judge_success_rate"], 1)

    def test_complete_json_fallback(self):
        self.run_main()
        self.checkpoint.unlink()
        _, calls = self.run_main()
        self.assertEqual(calls, 0)
        self.assertTrue(self.checkpoint.exists())

    def test_metadata_and_checkpoint_alignment(self):
        data, _ = self.run_main()
        self.assertEqual(data["items"], judge._load_rows(str(self.checkpoint)))
        self.assertIn("sensenova", data["metrics"]["judge_channel"])
        self.assertIn("test-model", data["metrics"]["judge_channel"])
        self.assertIsNone(data["meta"]["score_timeout_ms"])
        self.assertNotIn("synthetic-secret", json.dumps(data))
        self.assertNotIn("https://example.invalid", json.dumps(data))
        self.assertEqual(data["metric_counts"]["causal_accuracy"], {"numerator": 1, "denominator": 1})

    def test_subset_gold_keeps_bestof_compatibility(self):
        self.write_rows(self.answers, [self.answer("Q001"), self.answer("Q002")])
        data, calls = self.run_main()
        self.assertEqual(calls, 1)
        self.assertEqual(data["metrics"]["n"], 1)

    def test_partial_evidence_report_uses_ratio(self):
        spec = importlib.util.spec_from_file_location("judge_test_report", HERE.parent / "eval" / "eval_m1.py")
        evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluator)
        record = {**self.answer("Q001"), "evidence_chain": ["[src:synthetic]", "line two", "line three"]}
        self.write_rows(self.answers, [record])
        with contextlib.redirect_stdout(io.StringIO()):
            evaluator.cmd_score([str(self.answers), "--gold", str(self.gold), "--timeout-ms", "180000", "--out", str(self.scores)])
        data = json.loads(self.scores.read_text(encoding="utf-8"))
        self.assertAlmostEqual(data["metrics"]["evidence_completeness"]["mean_score"], 1 / 3, places=3)
        text = io.StringIO()
        with contextlib.redirect_stdout(text):
            evaluator.cmd_report_md([str(self.scores)])
        self.assertIn("0 < 比例 < 1", text.getvalue())
        self.assertNotIn("| 0.5 |", text.getvalue())

    def test_rule_timeout_is_separate_from_semantic_judge(self):
        spec = importlib.util.spec_from_file_location("judge_test_eval", HERE.parent / "eval" / "eval_m1.py")
        evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluator)
        answer = self.answer("Q001")
        self.assertFalse(evaluator.is_available(answer, 30000)[0])
        self.assertTrue(evaluator.is_available(answer, 180000)[0])
        self.assertTrue(evaluator.is_available({**answer, "latency_ms": 180000}, 180000)[0])
        self.assertFalse(evaluator.is_available({**answer, "latency_ms": 180001}, 180000)[0])
        data, _ = self.run_main()
        self.assertEqual(data["metrics"]["availability"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
