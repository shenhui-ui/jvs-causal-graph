"""Offline first-round checks; no credentials, production DB or network required."""
import asyncio
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import targeted_round as round1

VALID = json.dumps(dict.fromkeys(round1.judge.JUDGE_FIELDS, True))
TEXT = "原因：已发生变化。\n结果：完成调整。\n证据：R60001 [src: workspace/events #abc123]"
ENDPOINT = "http://127.0.0.1:9093/v1/chat/completions"


class RoundTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="targeted-test-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.prepared = [{"qid": q, "question": "synthetic question", "prompt": "synthetic prompt",
                          "event_ids": ["R60001"], "sources": ["workspace/events #abc123"],
                          "prompt_sha256": "synthetic"} for q in round1.TARGETS]
        round1.write_rows(self.root / "prepared-prompts.jsonl", self.prepared)
        round1.write_rows(self.root / "gold-subset-local.jsonl", [
            {"qid": q, "question": "synthetic question", "cause": "alpha", "effect": "beta", "evidence_hint": "gamma"}
            for q in round1.TARGETS])
        round1.write_json(self.root / "manifest.json", {"synthetic": True})
        self.manifest = {"generation_endpoint": ENDPOINT, "frozen_hashes": {}}
        for target, kwargs in (
            ("targeted_round.read_manifest", {"return_value": self.manifest}),
            ("targeted_round.verify_hashes", {}),
            ("targeted_round.bridge_health", {"return_value": {"ok": True}}),
            ("targeted_round.time.sleep", {}),
            ("targeted_round.answer.provider_conf", {"side_effect": self.conf}),
        ):
            p = patch(target, **kwargs)
            p.start()
            self.addCleanup(p.stop)
        self.traffic = []
        self.responses = []
        p = patch("urllib.request.urlopen", side_effect=self.response)
        p.start()
        self.addCleanup(p.stop)

    @staticmethod
    def conf(provider=None):
        provider = provider or os.environ["LLM_PROVIDER"]
        return {"provider": provider, "model": "deepseek-v4-pro" if provider == "sensenova" else "doubao",
                "url": "https://example.invalid/judge" if provider == "sensenova" else ENDPOINT, "key": "synthetic"}

    def response(self, request, *args, **kwargs):
        payload = json.loads(request.data)
        self.traffic.append((request.full_url, payload))
        text = self.responses.pop(0) if self.responses else (VALID if payload["model"] == "deepseek-v4-pro" else TEXT)
        if isinstance(text, BaseException):
            raise text
        return contextlib.closing(io.BytesIO(json.dumps({"choices": [{"message": {"content": text}}]}).encode()))

    def run_stage(self, stage):
        with contextlib.redirect_stdout(io.StringIO()):
            round1.run_stage(types.SimpleNamespace(work=str(self.root), stage=stage))
        return round1.load(self.root / (stage + "-status.json"))

    def test_visible_question_has_no_gold(self):
        q = {"qid": "Q001", "question": "q", "type": "why", "gold_answer": "SECRET", "cause": "SECRET"}
        self.assertEqual(round1.visible_question(q), {"qid": "Q001", "question": "q", "type": "why"})

    def test_channel_clears_and_restores_pollution(self):
        with patch.dict(os.environ, {"LLM_MODEL": "wrong", "LLM_NEIGHBORS": "1", "LLM_PROMPT_ORIGIN_EXAMPLE": "1"}):
            original = dict(os.environ)
            with round1.channel("generate", ENDPOINT):
                self.assertEqual(os.environ["LLM_MODEL"], "doubao")
                self.assertNotIn("LLM_NEIGHBORS", os.environ)
                self.assertEqual(round1.answer.MAX_RETRY, 0)
                with round1.channel("judge", ENDPOINT):
                    self.assertEqual(os.environ["LLM_PROVIDER"], "sensenova")
                self.assertEqual(os.environ["LLM_PROVIDER"], "doubao_sub")
            self.assertEqual(dict(os.environ), original)

    def test_endpoint_must_be_local(self):
        for url in ("https://example.com/v1/chat/completions", "http://user:pass@127.0.0.1/v1/chat/completions", ENDPOINT + "?token=x"):
            with self.subTest(url=url), self.assertRaises(ValueError), round1.channel("generate", url):
                pass

    def test_flag_default_preserves_prompt_and_scope(self):
        a = {"id": "R60001", "ts": "2026-01-01", "subject": "A", "action": "change", "object": "B", "source_hash": "abc123"}
        other = {**a, "id": "R69999"}
        q = {"question": "synthetic question"}
        with patch.dict(os.environ, {}, clear=True):
            default = round1.answer.build_prompt(q, a, [], {a["id"]: a, other["id"]: other})
            os.environ["LLM_PROMPT_EVIDENCE_FIRST"] = "0"
            self.assertEqual(default, round1.answer.build_prompt(q, a, [], {a["id"]: a}))
            os.environ["LLM_PROMPT_EVIDENCE_FIRST"] = "1"
            active = round1.answer.build_prompt(q, a, [], {a["id"]: a, other["id"]: other})
        self.assertNotIn("R60001", default)
        self.assertIn("R60001", active)
        self.assertNotIn("R69999", active)
        self.assertTrue(active.endswith(default.split("请只依据", 1)[1]))

    def test_privacy_fails_closed(self):
        gate = round1.PrivacyGate()
        # 占位串运行时拼接，避免源码内出现类密钥字面量（防误报密钥扫描器）
        _fake = ("s" + "k-" + "abcdefghijklmnopqrst")
        for text in ("Bearer abcdefghijklmnop", _fake, "user@example.com", "https://example.com/private"):
            with self.subTest(text=text), self.assertRaises(round1.PrivacyBlocked):
                gate.check(text)
        gate.check(TEXT)

    def test_reference_validation_is_not_semantic_score(self):
        result = round1.validate_candidate(TEXT, self.prepared[0])
        self.assertTrue(result["format_ok"] and result["reference_ok"])
        invalid = round1.validate_candidate(TEXT.replace("R60001", "R69999"), self.prepared[0])
        self.assertFalse(invalid["reference_ok"])
        self.assertNotIn("cause", invalid)
        self.assertFalse(round1.validate_candidate("extra\n" + TEXT, self.prepared[0])["format_ok"])

    def test_generate_exactly_eight_and_cannot_resume(self):
        result = self.run_stage("generate")
        self.assertEqual(result["status"], "completed")
        self.assertEqual((result["logical_calls"], result["chat_calls"], result["http_requests"]), (8, 8, 8))
        self.assertEqual(len(self.traffic), 8)
        self.assertTrue(all(p[1]["model"] == "doubao" for p in self.traffic))
        with self.assertRaises(FileExistsError):
            self.run_stage("generate")
        self.assertEqual(len(self.traffic), 8)

    def test_generation_failure_stops_without_transport_retry(self):
        self.responses = [TimeoutError("synthetic timeout")]
        with self.assertRaises(RuntimeError):
            self.run_stage("generate")
        status = round1.load(self.root / "generate-status.json")
        self.assertEqual(status["http_requests"], 1)
        self.assertEqual(status["completed_qids"], [])
        self.assertEqual(status["status"], "stopped")

    def test_generated_private_answer_blocks_remaining_questions(self):
        self.responses = ["Bearer abcdefghijklmnop"]
        with self.assertRaises(round1.PrivacyBlocked):
            self.run_stage("generate")
        self.assertEqual(len(self.traffic), 1)
        status = round1.load(self.root / "generate-status.json")
        self.assertEqual(status["status"], "blocked")
        self.assertTrue((self.root / "blocked-answer-local-only.json").exists())

    def test_prompt_gate_prevents_any_generation(self):
        self.prepared[0]["prompt"] = "Bearer abcdefghijklmnop"
        round1.write_rows(self.root / "prepared-prompts.jsonl", self.prepared)
        with self.assertRaises(round1.PrivacyBlocked):
            self.run_stage("generate")
        self.assertEqual(len(self.traffic), 0)
        self.assertFalse((self.root / "generate-started.json").exists())

    def test_judge_separate_channel_and_fixed_denominator(self):
        self.run_stage("generate")
        self.traffic.clear()
        status = self.run_stage("judge")
        self.assertEqual(status["logical_calls"], 8)
        self.assertTrue(all(p[1]["model"] == "deepseek-v4-pro" for p in self.traffic))
        score = round1.load(self.root / "scores-sn.json")
        self.assertEqual((score["denominator"], score["counts"]["joint"]), (8, 8))
        with self.assertRaises(FileExistsError):
            self.run_stage("judge")
        self.assertEqual(len(self.traffic), 8)

    def test_authorized_parse_retry_count_separated(self):
        self.run_stage("generate")
        self.responses = ["invalid JSON", VALID]
        status = self.run_stage("judge")
        self.assertEqual((status["logical_calls"], status["chat_calls"], status["http_requests"]), (8, 9, 9))

    def test_judge_failure_stops_and_keeps_all_qids(self):
        self.run_stage("generate")
        self.responses = ["{}"]
        with self.assertRaises(RuntimeError):
            self.run_stage("judge")
        score = round1.load(self.root / "scores-sn.json")
        self.assertEqual(score["denominator"], 8)
        self.assertEqual(len(score["items"]), 8)
        self.assertEqual(score["meta"]["http_requests"], 1)
        self.assertEqual(sum(r["error_type"] == "not_attempted" for r in score["items"]), 7)

    def test_interrupted_generation_cannot_resend(self):
        self.responses = [KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):
            self.run_stage("generate")
        with self.assertRaises(FileExistsError):
            self.run_stage("generate")
        self.assertEqual(len(self.traffic), 1)

    def test_changed_answer_rejected_before_judge(self):
        self.run_stage("generate")
        self.traffic.clear()
        with (self.root / "answers-llm.jsonl").open("a", encoding="utf-8") as f:
            f.write("\n")
        with self.assertRaises(ValueError):
            self.run_stage("judge")
        self.assertEqual(len(self.traffic), 0)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        class FakeApp:
            def __getattr__(self, _name):
                return lambda *a, **kw: lambda func: func
        class Response:
            def __init__(self, data, status_code):
                self.status_code = status_code
                self.data = data
        modules = {"uvicorn": types.SimpleNamespace(),
                   "fastapi": types.SimpleNamespace(FastAPI=FakeApp, Request=object),
                   "fastapi.responses": types.SimpleNamespace(JSONResponse=Response),
                   "pydantic": types.SimpleNamespace(BaseModel=object),
                   "doubao2api": types.ModuleType("doubao2api"),
                   "doubao2api.client": types.SimpleNamespace(DoubaoChatClient=object)}
        with patch.dict(sys.modules, modules), patch.object(sys, "argv", ["bridge", "synthetic-session", "9093"]), patch.dict(os.environ, {"DOUBAO_TARGETED_ROUND": "1", "DOUBAO_SUB_THINK": "3", "DOUBAO_FRESH": "1"}):
            spec = importlib.util.spec_from_file_location("synthetic_bridge", HERE / "aux_doubao_bridge.py")
            self.bridge = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.bridge)
        self.client = types.SimpleNamespace(chat_completion=AsyncMock(return_value=types.SimpleNamespace(text=TEXT, conversation_id="synthetic")))
        self.bridge._client = self.client
        self.env = patch.dict(os.environ, {"DOUBAO_FRESH": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def request(self, text="synthetic prompt", think=0):
        body = types.SimpleNamespace(messages=[{"content": text}], need_deep_think=think, model="doubao")
        with contextlib.redirect_stdout(io.StringIO()):
            return asyncio.run(self.bridge.chat_completions(body))

    def test_targeted_startup_disables_captcha_handler(self):
        client = types.SimpleNamespace(__aenter__=AsyncMock(), __aexit__=AsyncMock())
        factory = types.SimpleNamespace(from_session=unittest.mock.Mock(return_value=client))
        with patch.object(self.bridge, "DoubaoChatClient", factory):
            asyncio.run(self.bridge.startup())
        factory.from_session.assert_called_once_with("synthetic-session", captcha_handler=None, max_captcha_retries=0)
        client.__aenter__.assert_awaited_once()

    def test_bridge_budget_and_expert_fresh(self):
        for _ in range(8):
            self.request()
        result = self.request()
        self.assertEqual(result.status_code, 429)
        self.assertEqual(self.client.chat_completion.await_count, 8)
        kwargs = self.client.chat_completion.call_args.kwargs
        self.assertEqual(kwargs, {"need_deep_think": 3, "conversation_id": "", "click_clear_context": False})

    def test_bridge_fails_once_without_sensitive_error(self):
        self.client.chat_completion.side_effect = RuntimeError("synthetic-secret")
        result = self.request()
        self.assertEqual(self.client.chat_completion.await_count, 1)
        self.assertNotIn("synthetic-secret", str(result.data))
        self.assertEqual(result.status_code, 502)

    def test_bridge_privacy_and_mode_override(self):
        self.assertEqual(self.request("Bearer abcdefghijklmnop").status_code, 400)
        self.assertEqual(self.request(think=1).status_code, 400)
        self.assertEqual(self.client.chat_completion.await_count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
