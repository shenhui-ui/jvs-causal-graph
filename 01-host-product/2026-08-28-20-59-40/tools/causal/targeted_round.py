#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Explicit prepare/generate/judge stages for the fixed eight-question first round."""
import argparse
import ast
import contextlib
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.request
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import llm_answer as answer
import llm_judge as judge

TARGETS = ("Q001", "Q008", "Q009", "Q011", "Q014", "Q026", "Q027", "Q029")
FLAGS = {key: "1" for key in (
    "LLM_REV_EDGES", "LLM_FALLBACK", "LLM_PROMPT_CAUSE", "LLM_PROMPT_ORIGIN",
    "LLM_CHAIN_PRIORITIZE", "LLM_RETRIEVE_HYBRID", "LLM_EXT_V2",
    "LLM_PROMPT_EVIDENCE_FIRST")}
SOURCE_NAMES = ("llm_answer.py", "llm_judge.py", "targeted_round.py", "aux_doubao_bridge.py")
EVENT_RE = re.compile(r"\bR\d{5,}\b")
SOURCE_RE = re.compile(r"\[src[:：]\s*([^\]]+)\]", re.I)
SECRET_PATTERNS = {
    "bearer": r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}",
    "api_key": r"\bsk-[A-Za-z0-9_-]{16,}",
    "private_key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "credential": r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)[\"']?\s*[:=]\s*[\"']?[^\s\"',;}{]{12,}",
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    "url": r"https?://[^\s]+",
    "mobile": r"(?<!\d)1[3-9]\d{9}(?!\d)",
}


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def rows(path):
    result = {}
    for row in judge._load_rows(str(path), keys=("items", "answers", "results")):
        if not isinstance(row, dict) or not isinstance(row.get("qid"), str) or row["qid"] in result:
            raise ValueError("invalid or duplicate qid")
        result[row["qid"]] = row
    return result


def write_json(path, data):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def write_rows(path, records):
    judge._write_checkpoint(str(path), records)


class PrivacyBlocked(ValueError):
    def __init__(self, counts):
        self.counts = counts
        super().__init__("privacy gate blocked; payload withheld")


class PrivacyGate:
    def __init__(self):
        spec = importlib.util.spec_from_file_location("targeted_names", HERE.parent / "host/name_scan.py")
        scanner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scanner)
        self.name_pattern = scanner.build_pattern(scanner.load_names())
        self.patterns = {k: re.compile(v) for k, v in SECRET_PATTERNS.items()}

    def check(self, text):
        counts = {"known_names": len(self.name_pattern.findall(text))}
        counts.update({k: len(v.findall(text)) for k, v in self.patterns.items()})
        if any(counts.values()):
            raise PrivacyBlocked(counts)
        return counts


def source_manifest():
    paths = [HERE / name for name in SOURCE_NAMES]
    paths.extend((HERE / "m1_10_pipeline.py", HERE / "causal_graph.py", HERE.parent / "host/name_scan.py"))
    return {str(p): digest(p) for p in paths}


def verify_hashes(hashes):
    if not hashes or any(digest(p) != h for p, h in hashes.items()):
        raise ValueError("input or implementation fingerprint changed; execution refused")


@contextlib.contextmanager
def channel(stage, endpoint):
    if stage not in ("prepare", "generate", "judge"):
        raise ValueError("unknown stage")
    url = urlsplit(endpoint)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.path != "/v1/chat/completions" or url.username or url.password or url.query or url.fragment:
        raise ValueError("generation endpoint must be a loopback bridge")
    original = dict(os.environ)
    old_url = answer.PROVIDERS["doubao_sub"]["url"]
    old_retry = answer.MAX_RETRY
    old_gaps = answer.GAP_SECONDS, judge.GAP_SECONDS
    try:
        for key in list(os.environ):
            if key.startswith("LLM_") or key in ("DOUBAO_MODEL", "DOUBAO_SUB_URL"):
                os.environ.pop(key)
        os.environ.update(FLAGS)
        os.environ.update({"LLM_PROVIDER": "sensenova" if stage == "judge" else "doubao_sub",
                           "LLM_MODEL": "deepseek-v4-pro" if stage == "judge" else "doubao",
                           "LLM_MAX_TOKENS": "700", "LLM_JUDGE_MAX_TOKENS": "4096",
                           "LLM_JUDGE_CAUSE_RELAX": "0", "LLM_GAP": "16"})
        if stage != "judge":
            os.environ.setdefault("DOUBAO_SUB_KEY", "local")
        answer.PROVIDERS["doubao_sub"]["url"] = endpoint
        answer.MAX_RETRY = 6 if stage == "judge" else 0
        answer.GAP_SECONDS = judge.GAP_SECONDS = 16
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)
        answer.PROVIDERS["doubao_sub"]["url"] = old_url
        answer.MAX_RETRY = old_retry
        answer.GAP_SECONDS, judge.GAP_SECONDS = old_gaps


def visible_question(row):
    return {key: row[key] for key in ("qid", "question", "type") if key in row}


def validate_candidate(text, prepared):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    format_ok = len(lines) == 3 and all(re.match(r"^" + name + r"[:：]\s*\S", line)
                                           for name, line in zip(("原因", "结果", "证据"), lines))
    ids = set(EVENT_RE.findall(text))
    sources = set(SOURCE_RE.findall(text))
    known_ids = set(prepared["event_ids"])
    known_sources = set(prepared["sources"])
    return {"format_ok": format_ok, "cited_event_ids": sorted(ids),
            "unknown_event_ids": sorted(ids - known_ids),
            "unknown_sources": sorted(sources - known_sources),
            "reference_ok": bool(ids) and not (ids - known_ids) and bool(sources) and not (sources - known_sources),
            "source_citations": sorted(sources)}


def prepare(args):
    work = Path(args.work).resolve()
    if work.exists():
        raise FileExistsError("choose a new experiment directory")
    reference = load(args.reference)
    hashes = reference["input_hashes"]
    verify_hashes(hashes)
    db = Path(args.db).resolve()
    questions_path, gold_path, baseline_path = map(lambda p: Path(p).resolve(), (args.questions, args.gold, args.base))
    for p in (db, questions_path, gold_path, baseline_path):
        if str(p) not in hashes:
            raise ValueError("input absent from frozen reference")
    questions, gold, baseline = rows(questions_path), rows(gold_path), rows(baseline_path)
    if set(questions) != set(gold) or set(gold) != set(baseline) or len(gold) != 30:
        raise ValueError("expected identical frozen thirty-question coverage")
    gate = PrivacyGate()
    prepared, gold_subset = [], []
    with channel("prepare", args.endpoint):
        events = answer.load_events(str(db))
        for qid in TARGETS:
            q = visible_question(questions[qid])
            p = answer.prepare_question(str(db), q, events)
            # Frozen traces prove EXT_V2 and anchor/chain scope before any model call.
            trace = baseline[qid]["trace"]
            prior_kws = ast.literal_eval(trace.split(" kws=", 1)[1].split(" anchors=", 1)[0])
            scope = re.search(r"anchors=(\d+) chain=(\d+) nb=(\d+)", trace)
            actual = (len(p["anchors"]), len(p["chain"]), len(p["neighbors"]))
            if p["keywords"] != prior_kws or not scope or tuple(map(int, scope.groups())) != actual:
                raise ValueError("retrieval differs from frozen trace: " + qid)
            gate.check(p["prompt"])
            supplied_ids = {event["id"] for event in p["anchors"]}
            supplied_ids.update(event["id"] for _, event in p["neighbors"])
            supplied_ids.update(edge[key] for edge in p["chain"] for key in ("from_event", "to_event"))
            ids = sorted(supplied_ids & {event["id"] for event in events})
            sources = sorted(set(SOURCE_RE.findall(p["prompt"])))
            prepared.append({"qid": qid, "question": q["question"], "prompt": p["prompt"],
                             "event_ids": ids, "sources": sources,
                             "anchors": len(p["anchors"]), "edges": len(p["chain"]),
                             "prompt_sha256": hashlib.sha256(p["prompt"].encode("utf-8")).hexdigest()})
            g = {"qid": qid, "question": q["question"], **{k: gold[qid][k] for k in judge.JUDGE_FIELDS[:3]}}
            gate.check(judge.judge_prompt(q["question"], g, "synthetic answer"))
            gold_subset.append(g)
    work.mkdir(parents=False)
    write_rows(work / "prepared-prompts.jsonl", prepared)
    write_rows(work / "gold-subset-local.jsonl", gold_subset)
    manifest = {"created_at": now(), "targets": list(TARGETS), "round": 1,
                "generation_provider": "doubao_sub", "generation_model": "doubao",
                "generation_endpoint": args.endpoint, "judge_provider": "sensenova",
                "judge_model": "deepseek-v4-pro", "flags": FLAGS,
                "generation_max_tokens": 700, "judge_max_tokens": 4096,
                "logical_budget": {"generate": 8, "judge": 8}, "generation_transport_retries": 0,
                "judge_parse_retries": 1, "frozen_hashes": hashes,
                "source_hashes": source_manifest(), "prepared_hash": digest(work / "prepared-prompts.jsonl"),
                "gold_subset_hash": digest(work / "gold-subset-local.jsonl"),
                "privacy_preflight": "all eight generation and gold judge prompts passed",
                "retrieval_matches_frozen_traces": True,
                "comparison_scope": "targeted diagnostic, not blind evaluation; prompt includes supplied-event ID index"}
    verify_hashes(hashes)
    write_json(work / "manifest.json", manifest)
    print(json.dumps({"stage": "prepare", "status": "ready", "targets": len(prepared), "network_calls": 0}), flush=True)


def read_manifest(work):
    manifest = load(work / "manifest.json")
    if manifest["targets"] != list(TARGETS) or manifest["flags"] != FLAGS or manifest["logical_budget"] != {"generate": 8, "judge": 8}:
        raise ValueError("experiment scope changed")
    verify_hashes(manifest["frozen_hashes"])
    verify_hashes(manifest["source_hashes"])
    verify_hashes({str(work / "prepared-prompts.jsonl"): manifest["prepared_hash"],
                   str(work / "gold-subset-local.jsonl"): manifest["gold_subset_hash"]})
    return manifest


def bridge_health(endpoint):
    url = endpoint.split("/v1/", 1)[0] + "/health"
    with urllib.request.urlopen(url, timeout=5) as response:
        data = json.loads(response.read())
    if not (data.get("ok") is True and data.get("think") == 3 and data.get("fresh") is True
            and data.get("targeted") is True and data.get("upstream_attempts") == 0):
        raise ValueError("bridge is not a fresh isolated expert-mode targeted worker")
    return data


def run_stage(args):
    work = Path(args.work).resolve()
    stage = args.stage
    manifest = read_manifest(work)
    prepared = rows(work / "prepared-prompts.jsonl")
    if set(prepared) != set(TARGETS):
        raise ValueError("invalid prepared scope")
    gate = PrivacyGate()
    records = []
    status = {"stage": stage, "started_at": now(), "status": "running", "logical_calls": 0,
              "chat_calls": 0, "http_requests": 0, "completed_qids": [], "failed_qid": None,
              "error_type": "", "network_budget_scope": "HTTP count excludes upstream client internals"}
    with channel(stage, manifest["generation_endpoint"]):
        expected = "sensenova" if stage == "judge" else "doubao_sub"
        conf = answer.provider_conf(expected)
        if conf["provider"] != expected or conf["model"] != ("deepseek-v4-pro" if stage == "judge" else "doubao"):
            raise ValueError("effective provider/model mismatch")
        if stage == "generate":
            # Verify judge credentials exist before spending the generation budget.
            with channel("judge", manifest["generation_endpoint"]):
                answer.provider_conf("sensenova")
            bridge_health(manifest["generation_endpoint"])
            for p in prepared.values():
                gate.check(p["prompt"])
        else:
            generation_status = load(work / "generate-status.json")
            if generation_status["status"] != "completed" or generation_status["completed_qids"] != list(TARGETS):
                raise ValueError("generation not complete")
            answers = rows(work / "answers-llm.jsonl")
            if digest(work / "answers-llm.jsonl") != generation_status["output_sha256"] or set(answers) != set(TARGETS):
                raise ValueError("generated answers changed")
            gold = rows(work / "gold-subset-local.jsonl")
            for qid in TARGETS:
                gate.check(judge.judge_prompt(prepared[qid]["question"], gold[qid], answers[qid]["answer_text"]))
        # Exclusive marker prevents retries after a timeout or interruption from double-spending.
        with (work / (stage + "-started.json")).open("x", encoding="utf-8") as f:
            json.dump({"started_at": now(), "targets": TARGETS, "manifest_sha256": digest(work / "manifest.json")}, f)
        write_json(work / (stage + "-status.json"), status)
        original_open, original_chat = urllib.request.urlopen, judge.chat
        http_limit = 8 if stage == "generate" else 112

        def counted_open(request, *a, **kw):
            if not isinstance(request, urllib.request.Request) or request.full_url != conf["url"]:
                raise ValueError("unexpected model endpoint")
            payload = json.loads(request.data)
            if payload.get("model") != conf["model"]:
                raise ValueError("unexpected model")
            for message in payload["messages"]:
                gate.check(message["content"])
            if status["http_requests"] >= http_limit:
                raise ValueError("HTTP budget exhausted")
            status["http_requests"] += 1
            write_json(work / (stage + "-status.json"), status)
            return original_open(request, *a, **kw)

        def counted_chat(messages, **kwargs):
            if status["chat_calls"] >= (8 if stage == "generate" else 16):
                raise ValueError("chat budget exhausted")
            status["chat_calls"] += 1
            write_json(work / (stage + "-status.json"), status)
            return answer.chat(messages, provider=expected, **kwargs)

        urllib.request.urlopen, judge.chat = counted_open, counted_chat
        try:
            for qid in TARGETS:
                status["failed_qid"] = qid
                status["logical_calls"] += 1
                write_json(work / (stage + "-status.json"), status)
                started = time.monotonic()
                if stage == "generate":
                    p = prepared[qid]
                    text = counted_chat([{"role": "user", "content": p["prompt"]}], max_tokens=700, temperature=0.3)
                    if not isinstance(text, str) or not text.strip():
                        raise judge.JudgeResponseError("response_empty")
                    try:
                        gate.check(text)
                    except PrivacyBlocked:
                        write_json(work / "blocked-answer-local-only.json", {"qid": qid, "answer_text": text})
                        raise
                    validation = validate_candidate(text, p)
                    record = {"qid": qid, "question": p["question"], "answer_text": text,
                              "evidence_chain": ["[src: " + s + "]" for s in validation["source_citations"]],
                              "pipeline_ok": True, "latency_ms": int((time.monotonic() - started) * 1000),
                              "validation": validation, "prompt_sha256": p["prompt_sha256"]}
                else:
                    a, g = answers[qid], gold[qid]
                    prompt = judge.judge_prompt(prepared[qid]["question"], g, a["answer_text"])
                    gate.check(prompt)
                    record = judge.judge_answer(prompt, 4096, parse_retries=1)
                    record.update({"qid": qid, "judge_ms": int((time.monotonic() - started) * 1000),
                                   "latency_ms": a["latency_ms"], "input_fingerprint": judge.input_fingerprint(
                                       qid, g, a["answer_text"], {"provider": expected, "model": conf["model"],
                                       "prompt": prompt, "endpoint_sha256": hashlib.sha256(conf["url"].encode()).hexdigest()})})
                records.append(record)
                filename = "answers-llm.jsonl" if stage == "generate" else "scores-sn.json.checkpoint.jsonl"
                write_rows(work / filename, records)
                if stage == "judge" and record["judge_ok"] is not True:
                    status["error_type"] = record["error_type"]
                    raise RuntimeError("judge did not complete")
                status["completed_qids"].append(qid)
                status["failed_qid"] = None
                write_json(work / (stage + "-status.json"), status)
                print(json.dumps({"stage": stage, "qid": qid, "completed": len(records),
                                  "http_requests": status["http_requests"]}), flush=True)
                if qid != TARGETS[-1]:
                    time.sleep(16)
            status["status"] = "completed"
        except BaseException as exc:
            status["status"] = "blocked" if isinstance(exc, PrivacyBlocked) else "stopped"
            status["error_type"] = status["error_type"] or ("privacy_blocked" if isinstance(exc, PrivacyBlocked)
                                                             else judge.classify_judge_error(exc))
            if isinstance(exc, PrivacyBlocked):
                status["privacy_counts"] = exc.counts
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                status["error_type"] = "interrupted_no_auto_resume"
            raise
        finally:
            urllib.request.urlopen, judge.chat = original_open, original_chat
            status["finished_at"] = now()
            try:
                verify_hashes(manifest["frozen_hashes"])
                status["frozen_hashes_unchanged"] = True
            except Exception:
                status["frozen_hashes_unchanged"] = False
                status["status"] = "stopped"
                status["error_type"] = "frozen_input_changed"
            if stage == "generate" and records:
                status["output_sha256"] = digest(work / "answers-llm.jsonl")
            write_json(work / (stage + "-status.json"), status)
            if stage == "judge":
                by_id = {r["qid"]: r for r in records}
                items = [by_id.get(q, {"qid": q, **dict.fromkeys(judge.JUDGE_FIELDS, False),
                                      "judge_ok": False, "error_type": "not_attempted", "judge_attempts": 0}) for q in TARGETS]
                counts = {k: sum(r.get("judge_ok") is True and r.get(k) is True for r in items) for k in judge.JUDGE_FIELDS}
                counts["joint"] = sum(r.get("judge_ok") is True and all(r[k] is True for k in judge.JUDGE_FIELDS[:3]) for r in items)
                counts["judge_ok"] = sum(r.get("judge_ok") is True for r in items)
                write_json(work / "scores-sn.json", {"meta": status, "denominator": 8, "counts": counts, "items": items})
    if status["status"] != "completed":
        raise ValueError("stage did not complete")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("prepare", "generate", "judge"))
    parser.add_argument("--work", required=True)
    parser.add_argument("--reference")
    parser.add_argument("--db")
    parser.add_argument("--questions")
    parser.add_argument("--gold")
    parser.add_argument("--base")
    parser.add_argument("--endpoint", default="http://127.0.0.1:9092/v1/chat/completions")
    args = parser.parse_args()
    try:
        if args.stage == "prepare":
            if not all((args.reference, args.db, args.questions, args.gold, args.base)):
                parser.error("prepare requires reference, db, questions, gold and base")
            prepare(args)
        else:
            run_stage(args)
    except Exception as exc:
        print(json.dumps({"stage": args.stage, "status": "stopped", "error_class": type(exc).__name__,
                          "error_type": "privacy_blocked" if isinstance(exc, PrivacyBlocked) else judge.classify_judge_error(exc)}, ensure_ascii=True), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
