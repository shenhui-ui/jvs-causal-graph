# -*- coding: utf-8 -*-
"""genN_bestof.py — judge-in-loop best-of-N（round-6 基建）
对指定 qid 各生成 N 份独立样本 → 逐样本 pro 判分 → 取三要素最全者写入最终 answers。
用法:
  python genN_bestof.py --db <v3.db> --questions <qset.jsonl> --gold <gold.jsonl> \
      --base <answers-llm.jsonl> --targets Q008,Q013,... --n 3 --work <workdir> \
      --final <out/answers-llm.jsonl>
生成通道/判分通道由环境变量决定(LLM_PROVIDER/DOUBAO_SUB_URL/LLM_MAX_TOKENS/LLM_GAP 等)。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def load_rows(path, keys=("items", "answers", "results")):
    """Read JSONL, a JSON array, or a complete JSON object containing rows."""
    with open(path, encoding="utf-8-sig") as f:
        raw = f.read().strip()
    if not raw:
        return []
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return [json.loads(line) for line in raw.splitlines() if line.strip()]
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in keys:
            rows = obj.get(key)
            if isinstance(rows, list):
                return rows
        if obj.get("qid") is not None:
            return [obj]
    raise ValueError("输入必须是 JSONL、JSON 数组或带条目数组的 JSON 对象")


def load_judge_rows(path):
    """兼容 llm_judge 的 JSONL checkpoint 与完成态 JSON。"""
    return load_rows(path, keys=("items", "results"))


def dump_rows(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--base", required=True, help="含其他题答案的种子 answers 文件")
    ap.add_argument("--targets", required=True, help="逗号分隔 qid")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--work", required=True)
    ap.add_argument("--final", required=True)
    args = ap.parse_args()

    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    base = load_rows(args.base)
    for row in base:
        if not isinstance(row, dict) or row.get("qid") is None:
            raise ValueError("base 输入含无 qid 条目")
        row["qid"] = str(row["qid"])
    base_qids = [row["qid"] for row in base]
    if len(set(base_qids)) != len(base_qids):
        raise ValueError("base 输入存在重复 qid")
    targets = [str(target) for target in targets]
    seed = [r for r in base if str(r["qid"]) not in targets]
    gold_rows = load_rows(args.gold)
    gold = {}
    for row in gold_rows:
        if not isinstance(row, dict) or row.get("qid") is None:
            raise ValueError("gold 输入含无 qid 条目")
        qid = str(row["qid"])
        row["qid"] = qid
        if qid in gold:
            raise ValueError("gold 输入存在重复 qid: " + qid)
        gold[qid] = row
    gold_sub = [g for g in gold.values() if g["qid"] in targets]
    os.makedirs(args.work, exist_ok=True)
    sub_path = os.path.join(args.work, "gold_sub.jsonl")
    dump_rows(sub_path, gold_sub)

    best = {}  # qid -> (answer_row, score)
    env = dict(os.environ)
    for i in range(1, args.n + 1):
        d = os.path.join(args.work, f"s{i}")
        os.makedirs(d, exist_ok=True)
        ans_path = os.path.join(d, "answers-llm.jsonl")
        dump_rows(ans_path, seed)
        print(f"[gen {i}/{args.n}] ...", flush=True)
        subprocess.run([PY, os.path.join(HERE, "llm_answer.py"),
                        "--db", args.db, "--questions", args.questions, "--out", d],
                       env=env, check=True)
        j_path = os.path.join(d, "judge.json")
        dump_rows(j_path, [])
        subprocess.run([PY, os.path.join(HERE, "llm_judge.py"),
                        "--answers", ans_path, "--gold", sub_path, "--out", j_path],
                       env=env, check=True)
        rows = load_judge_rows(j_path)
        if not rows:
            continue
        res = {r["qid"]: r for r in rows}
        all_rows = load_rows(ans_path)
        amap = {r["qid"]: r for r in all_rows}
        for q in targets:
            j = res.get(q)
            a = amap.get(q)
            if j is None or a is None:
                continue
            score = (int(j.get("cause")) + int(j.get("effect")) + int(j.get("evidence_hint")),
                     int(j.get("available")), len(a.get("answer_text") or ""))
            if q not in best or score > best[q][0]:
                best[q] = (score, a)
        print(f"[gen {i}] done", flush=True)

    if len(best) != len(targets):
        print("WARN best missing:", [q for q in targets if q not in best], flush=True)
    final = []
    for r in base:
        q = r["qid"]
        if q in best:
            final.append(best[q][1])
        else:
            final.append(r)
    dump_rows(args.final, final)
    for q in targets:
        if q in best:
            print(f"BEST {q}: {best[q][0]}", flush=True)
        else:
            print(f"BEST {q}: (无样本)", flush=True)
    print("OK final →", args.final, flush=True)


if __name__ == "__main__":
    main()
