#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
replay_harness.py —— 因果层 PoC 的「会话回放 harness」框架

--------------------------------------------------------------------------
【职责边界】
本文件只做『调度』与『输出契约』:
  * 按时间序(ts 升序)回放一批脱敏样本(对话/日志)给『管线插件』;
  * 在每个问题上调用管线的 answer(),收集答案 / 证据链 / 耗时;
  * 产出可被评测工具直接判分的 predictions.jsonl。
本文件『不』实现任何抽取 / 因果 / 问答逻辑。那些属于:
  * S3:样本→证据的抽取(数据结构化);
  * S4:因果推断(变更归因);
  * S6:问答(把因果结论组装成自然语言答案)。
接入方在 S5/S6 完成后,用 --plugin 指向自己的 Pipeline 子类即可,本框架无需改动。

--------------------------------------------------------------------------
【与 memory_eval 判分的对接方式】
评测工具位于 tools\\eval\\memory_eval.py,通过 --plugin 加载,
其打分命令形如:
    python tools\\eval\\memory_eval.py score --schema causal \\
        --predictions <out>/predictions.jsonl --gold <gold-answers.jsonl>

本框架产出的 `predictions.jsonl` 严格按 memory_eval 的 causal schema 约定,
每条是一行 JSON:
    {"qid": "<question_id>", "predicted_answer": "<answer_text>"}
其中:
    * qid             ← 题目唯一标识(默认为 DefaultQuestionBank 的 question_id);
    * predicted_answer← 管线在本题上返回的 answer_text。
证据链(evidence_chain)与耗时(latency_ms)不在判分 schema 内,但它们也写入
`answers.jsonl`,供展示 / 审计 / 二次分析使用(判分只认 predictions.jsonl)。

`answers.jsonl` 字段:
    question_id, answer_text, evidence_chain, latency_ms, difficulty, gold_key
其中 gold_key 为可选三元 {"当时":.., "后来":.., "因为":..},
若题目自带标准答案(c01b_answer)而未显式给出 gold_key,框架会用
extract_c01b_gold_key() 自动从标准答案中抽取该三元;抽取不到则为 null。

--------------------------------------------------------------------------
【用法】
    python replay_harness.py run samples.jsonl --out ./out
    python replay_harness.py run samples.jsonl --out ./out \\
        --plugin mypipeline.py --plugin-class MyPipeline
    python replay_harness.py run samples.jsonl --out ./out \\
        --questions my_questions.jsonl
    python replay_harness.py run samples.jsonl --out ./out --dry-run
samples.jsonl 每条(按 kind 校验):
    {"id": "s001", "ts": "2026-08-01T10:00:00", "content": "...", "kind": "meeting"}
    kind ∈ {meeting, chat, doc}

仅标准库(python -m py_compile 通过,运行时不依赖第三方)。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

# ---------------------------------------------------------------------------
# 基础数据结构
# ---------------------------------------------------------------------------

# kind 白名单
VALID_KINDS = ("meeting", "chat", "doc")


@dataclass
class Sample:
    """一条待回放的样本(脱敏对话 / 日日志)。"""

    id: str
    ts: str
    content: str
    kind: str

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Sample":
        if not isinstance(d, dict):
            raise ValueError(f"samples 行必须是对象, got {type(d).__name__}")
        for key in ("id", "ts", "content", "kind"):
            if key not in d:
                raise ValueError(f"samples 行缺少字段: {key}")
        kind = str(d["kind"])
        if kind not in VALID_KINDS:
            raise ValueError(f"非法 kind={kind!r}, 应为 {VALID_KINDS}")
        return cls(id=str(d["id"]), ts=str(d["ts"]), content=str(d["content"]), kind=kind)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "ts": self.ts, "content": self.content, "kind": self.kind}


@dataclass
class Question:
    """一道题目。gold_key 与 c01b_answer 可选,二者至少其一即可产出 gold_key。

    gold_key 形如 {"当时": .., "后来": .., "因为": ..}。
    """

    question_id: str
    question_text: str
    difficulty: str = ""
    gold_key: Optional[Dict[str, str]] = None
    c01b_answer: Optional[str] = None  # 标准答案文本,用于自动抽取 gold_key

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Question":
        for key in ("question_id", "question_text"):
            if key not in d:
                raise ValueError(f"题目行缺少字段: {key}")
        return cls(
            question_id=str(d["question_id"]),
            question_text=str(d["question_text"]),
            difficulty=str(d.get("difficulty", "")),
            gold_key=d.get("gold_key"),
            c01b_answer=d.get("c01b_answer"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question_text": self.question_text,
            "difficulty": self.difficulty,
            "gold_key": self.gold_key,
            "c01b_answer": self.c01b_answer,
        }


# Answer 的契约:answer() 返回的字典,至少含以下键
#   answer_text    : 回答文本
#   evidence_chain : 证据链(list[str] 或 list[dict])
#   latency_ms     : 本题耗时(毫秒,可选,框架未测时可为 -1)
#   detail         : 附加说明(dict 或 str)
Answer = Dict[str, Any]


# ---------------------------------------------------------------------------
# 管线插件基类
# ---------------------------------------------------------------------------

class Pipeline(ABC):
    """管线插件基类。子类实现"吃样本 + 答问题"两件事。

    on_sample():  按 ts 升序被调用一次,用于吸收样本、累积状态(S3/S4 逻辑)。
    answer():     接收一道 Question,返回一个 Answer 字典。S6 逻辑。
    """

    #: 插件名,默认取子类类名;子类可覆盖以展示更友好的名字。
    name: str = ""

    @abstractmethod
    def on_sample(self, sample: Sample) -> None:
        """吸收一个样本。不返回;异常会中止回放(可自行 try)。"""

    @abstractmethod
    def answer(self, question: Question) -> Answer:
        """回答一道题,返回 Answer 字典。

        满足契约即可:answer_text / evidence_chain / latency_ms / detail。
        """

    def close(self) -> None:
        """回放结束时调用;默认空实现,子类可覆写做清理/落盘。"""


class StubPipeline(Pipeline):
    """内置占位管线:让框架在未接入真实 S3/S4/S6 前先跑通。

    on_sample  仅记录样本 id(不实现任何抽取/因果)。
    answer     返回固定的未接入提示并每次打一次告警,便于提醒接入方。
    """

    name = "StubPipeline"

    def on_sample(self, sample: Sample) -> None:
        # 占位:不做任何处理。真实管线在这里做抽取与因果归因(S3/S4/S6)。
        self._seen = getattr(self, "_seen", 0) + 1

    def answer(self, question: Question) -> Answer:
        warnings.warn(
            f"[StubPipeline] 问题 {question.question_id!r} 未接入:"
            "请在 S5/S6 完成后实现真正的 Pipeline 子类,并用 --plugin 加载。",
            stacklevel=2,
        )
        return {
            "answer_text": "(未接入:请在 S5/S6 完成后实现)",
            "evidence_chain": [],
            "latency_ms": -1,
            "detail": "StubPipeline 为占位实现,未产出真实证据链。",
        }

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# 插件加载:--plugin mypipeline.py(按类名匹配 Pipeline 子类)
# ---------------------------------------------------------------------------

def load_plugin_module(path: str) -> Any:
    """从任意 Python 文件加载模块,返回 module 对象。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"插件文件不存在: {p}")
    mod_name = "__replay_plugin_" + re.sub(r"\W+", "_", p.stem)
    spec = importlib.util.spec_from_file_location(mod_name, str(p))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法从 {p} 构建模块")
    # 关键:本框架常以 `python replay_harness.py` 运行,此时 __name__ == "__main__"。
    # 若不注册,插件里的 `import replay_harness` 会在 sys.path 上再加载一份独立模块,
    # 导致它的 Pipeline 基类与这里的 Pipeline 不是同一个类对象,issubclass 判不中。
    # 这里把当前运行模块挂到规范名 replay_harness 下,让插件复用同一份基类。
    _canonical = "replay_harness"
    _running = sys.modules.get(__name__)
    if _running is not None:
        sys.modules.setdefault(_canonical, _running)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_pipeline_class(module: Any, class_name: Optional[str] = None) -> type:
    """扫描模块,找出 Pipeline 的子类。

    * 显式给 class_name:按类名精确匹配。
    * 未给 class_name:模块里恰好只有一个 Pipeline 子类时取唯一者;
      有多个时报错,提示用 --plugin-class 指定。
    """
    candidates: List[type] = []
    for obj in vars(module).values():
        if isinstance(obj, type) and issubclass(obj, Pipeline) and obj is not Pipeline:
            candidates.append(obj)

    if class_name:
        for cls in candidates:
            if cls.__name__ == class_name:
                return cls
        raise ValueError(
            f"插件模块中未找到名为 {class_name!r} 的 Pipeline 子类;"
            f"实际候选: {[c.__name__ for c in candidates]}"
        )

    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError("插件模块中没有找到 Pipeline 子类(需定义一个 StubPipeline 之外的子类)")
    raise ValueError(
        f"插件模块定义了多个 Pipeline 子类: {[c.__name__ for c in candidates]},"
        "请用 --plugin-class 指定其一。"
    )


def build_pipeline(args: argparse.Namespace) -> Pipeline:
    """依据 CLI 构造管线实例:有 --plugin 用插件,否则用内置 StubPipeline。"""
    if args.plugin:
        module = load_plugin_module(args.plugin)
        cls = find_pipeline_class(module, args.plugin_class)
        pipeline = cls()
    else:
        warnings.warn(
            "[replay] 未指定 --plugin,使用内置 StubPipeline(仅占位)。",
            stacklevel=2,
        )
        pipeline = StubPipeline()
    return pipeline


# ---------------------------------------------------------------------------
# 默认题库:DefaultQuestionBank(C01b 风格,13 道)
# ---------------------------------------------------------------------------

# 12 个本次会话中被讨论并最终敲定的变更,
# 每个变更给出 (问题文本的"改动对象", gold_key 三元)。
# gold_key = {"当时": 改动前, "后来": 改动后, "因为": 变更原因}
_DEFAULT_CHANGES: List[Dict[str, Any]] = [
    {
        "subject": "支付超时重试",
        "gold_key": {
            "当时": "支付超时后反复重试",
            "后来": "改为主动查单补偿",
            "因为": "重试无法收敛渠道侧不确定态,主动查单才能落到确定态",
        },
    },
    {
        "subject": "消息队列选型",
        "gold_key": {
            "当时": "计划采用 Kafka",
            "后来": "改回 RocketMQ",
            "因为": "支付链路需要保序与事务回查,RocketMQ 更契合",
        },
    },
    {
        "subject": "延迟目标",
        "gold_key": {
            "当时": "延迟目标 300ms",
            "后来": "收紧到 250ms",
            "因为": "线上实测 p99 更激进,需留出余量",
        },
    },
    {
        "subject": "上线时间",
        "gold_key": {
            "当时": "计划 7 月上线",
            "后来": "推迟到 8 月",
            "因为": "合规与灰度验证时间不足,故后移",
        },
    },
    {
        "subject": "蓝绿切流窗口",
        "gold_key": {
            "当时": "蓝绿切流窗口定在 8/5",
            "后来": "调整到 8/6",
            "因为": "8/5 的 CD 物料未就绪",
        },
    },
    {
        "subject": "流量准入策略",
        "gold_key": {
            "当时": "仅白名单准入",
            "后来": "改为黑白名单结合",
            "因为": "白名单覆盖不足,需要黑名单兜底拦截异常流量",
        },
    },
    {
        "subject": "切流比例",
        "gold_key": {
            "当时": "切流 10%",
            "后来": "提升到 30%",
            "因为": "观察窗口内无异常,故加大切流",
        },
    },
    {
        "subject": "异常处置方式",
        "gold_key": {
            "当时": "一遇到异常即回滚",
            "后来": "改为先观察",
            "因为": "早期错误率波动可接受,直接回滚成本过高",
        },
    },
    {
        "subject": "合规接口负责人",
        "gold_key": {
            "当时": "合规后端由李琳负责",
            "后来": "改由陈接手",
            "因为": "李琳转去其他项目,由陈接替",
        },
    },
    {
        "subject": "回调超时阈值",
        "gold_key": {
            "当时": "回调超时 5s",
            "后来": "放宽到 8s",
            "因为": "渠道回调存在抖动,5s 易误判",
        },
    },
    {
        "subject": "灰度开放范围",
        "gold_key": {
            "当时": "灰度计划直接开放全量",
            "后来": "改为先开放前 5%",
            "因为": "先放 5% 即可验证核心支付链路",
        },
    },
    {
        "subject": "网络部署策略",
        "gold_key": {
            "当时": "单机房部署",
            "后来": "改为双机房容灾",
            "因为": "为提升可用性,降低单点故障风险",
        },
    },
]

# 第 13 道:跨会话的"追溯 + 归因"题。
_THIRTEENTH: Dict[str, Any] = {
    "question": "上周跟张总聊的支付超时重试方案,后来改成了什么、为什么改?",
    "gold_key": {
        "当时": "上周与张总聊的支付超时重试方案(超时后反复重试)",
        "后来": "改为主动查单补偿",
        "因为": "重试无法收敛渠道侧不确定态,主动查单才能落到确定态",
    },
}


class DefaultQuestionBank:
    """内置 13 道 C01b 风格题目。

    前 12 道统一为「为什么……改了?」,分别对 _DEFAULT_CHANGES 中的 12 个变更。
    第 13 道为跨会话的"后来改成了什么、为什么改"追溯题。
    difficulty:前 12 道 medium(单点归因),第 13 道 hard(跨会话综合)。
    """

    def load(self) -> List[Question]:
        questions: List[Question] = []
        for i, ch in enumerate(_DEFAULT_CHANGES, start=1):
            qid = f"c01b_q{i:02d}"
            questions.append(
                Question(
                    question_id=qid,
                    question_text=f"为什么{ch['subject']}改了?",
                    difficulty="medium",
                    gold_key=ch["gold_key"],
                )
            )
        questions.append(
            Question(
                question_id="c01b_q13",
                question_text=_THIRTEENTH["question"],
                difficulty="hard",
                gold_key=_THIRTEENTH["gold_key"],
            )
        )
        return questions


# ---------------------------------------------------------------------------
# C01b 标准答案 → 「当时/后来/因为」三元 的自动抽取
# ---------------------------------------------------------------------------

def extract_c01b_gold_key(text: str) -> Optional[Dict[str, str]]:
    """从 C01b 标准答案文本中自动抽取「当时/后来/因为」三元。

    采用"定位标签 + 截取相邻标签之间的片段"的方式,兼容两种写法:
      * 逐行: 当时:...\\n 后来:...\\n 因为:...
      * 同一行内联: 当时:...。后来:...。因为:...。
    抽取不到任何键时返回 None。
    注意:这只是启发式解析;若题目自带结构化 gold_key(见 Question.gold_key),
    应优先使用,无需依赖本函数。
    """
    if not text:
        return None
    labels = ("当时", "后来", "因为")
    pos = {label: text.find(label) for label in labels}
    # 按出现位置从早到晚排序(未出现的排最后)
    order = sorted(labels, key=lambda l: pos[l] if pos[l] >= 0 else len(text) + 1)
    found: Dict[str, str] = {}
    for i, label in enumerate(order):
        idx = pos[label]
        if idx < 0:
            continue
        next_idx = pos[order[i + 1]] if (i + 1 < len(order) and pos[order[i + 1]] >= 0) else len(text)
        segment = text[idx + len(label):next_idx]
        # 去掉标签后的冒号/空白,以及片段末尾的标点
        val = segment.lstrip(":： \t").strip().rstrip("。；;,.， 　")
        if val:
            found[label] = val
    return found or None


def annotate_gold(question: Question) -> Optional[Dict[str, str]]:
    """确定一道题最终使用的 gold_key(可选)。

    优先级:显式 gold_key → 从 c01b_answer 自动抽取 → None。
    """
    if question.gold_key:
        return question.gold_key
    if question.c01b_answer:
        return extract_c01b_gold_key(question.c01b_answer)
    return None


# ---------------------------------------------------------------------------
# 样本 / 题库 / 答案 的读写
# ---------------------------------------------------------------------------

def iter_jsonl(path: str) -> Iterator[Dict[str, Any]]:
    # utf-8-sig: 兼容 Windows 工具生成的带 BOM 的 jsonl
    with open(path, encoding="utf-8-sig") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno} 不是合法 JSON") from exc


def load_samples(path: str) -> List[Sample]:
    samples = [Sample.from_dict(d) for d in iter_jsonl(path)]
    if not samples:
        raise ValueError(f"samples 为空: {path}")
    return samples


def load_questions(path: Optional[str]) -> List[Question]:
    """加载题库。--questions 指定 jsonl 则覆盖默认题库。"""
    if path:
        questions = [Question.from_dict(d) for d in iter_jsonl(path)]
        if not questions:
            raise ValueError(f"--questions 为空: {path}")
        return questions
    return DefaultQuestionBank().load()


def write_jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# run 子命令
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    samples = load_samples(args.samples)
    # 按 ts 升序回放(时间上先发生的先送入管线)
    samples.sort(key=lambda s: s.ts)
    questions = load_questions(args.questions)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    pipeline = build_pipeline(args)
    log_lines: List[str] = []
    log_lines.append(f"[replay] 插件: {pipeline.name} | 样本数: {len(samples)} | 题目数: {len(questions)}")

    seen_ids: set = set()
    # 逐条回放样本,记录处理轨迹
    for sample in samples:
        if sample.id in seen_ids:
            log_lines.append(f"[replay] 重复样本 id={sample.id} (ts={sample.ts}), 跳过")
            continue
        seen_ids.add(sample.id)
        t0 = time.perf_counter()
        try:
            pipeline.on_sample(sample)
        except Exception as exc:  # 管线单条样本异常不中断整体回放
            ms = (time.perf_counter() - t0) * 1000.0
            log_lines.append(
                f"[replay] sample id={sample.id} ts={sample.ts} kind={sample.kind} "
                f"FAILED after {ms:.1f}ms: {type(exc).__name__}: {exc}"
            )
            continue
        ms = (time.perf_counter() - t0) * 1000.0
        log_lines.append(
            f"[replay] sample id={sample.id} ts={sample.ts} kind={sample.kind} ok {ms:.1f}ms"
        )

    # 逐题提问,收集答案
    answer_rows: List[Dict[str, Any]] = []
    pred_rows: List[Dict[str, Any]] = []
    for q in questions:
        t0 = time.perf_counter()
        try:
            ans = pipeline.answer(q)
        except Exception as exc:
            ms = (time.perf_counter() - t0) * 1000.0
            log_lines.append(
                f"[replay] question {q.question_id} FAILED after {ms:.1f}ms: "
                f"{type(exc).__name__}: {exc}"
            )
            ans = {
                "answer_text": f"(管线答题异常: {type(exc).__name__}: {exc})",
                "evidence_chain": [],
                "latency_ms": ms,
                "detail": "answer() 抛出异常,已捕获。",
            }
        ms = time.perf_counter() - t0
        answer_text = str(ans.get("answer_text", ""))
        evidence = ans.get("evidence_chain", [])
        latency = ans.get("latency_ms", round(ms * 1000.0, 2))

        answer_rows.append(
            {
                "question_id": q.question_id,
                "answer_text": answer_text,
                "evidence_chain": evidence,
                "latency_ms": latency,
                "difficulty": q.difficulty,
                "gold_key": annotate_gold(q),
            }
        )
        # 供 memory_eval 判分的最小契约
        pred_rows.append({"qid": q.question_id, "predicted_answer": answer_text})
        log_lines.append(
            f"[replay] question {q.question_id} answered in {latency}ms "
            f"(evidence={len(evidence) if isinstance(evidence, list) else 'n/a'})"
        )

    pipeline.close()

    answers_path = out_dir / "answers.jsonl"
    pred_path = out_dir / "predictions.jsonl"
    log_path = out_dir / "replay_log.txt"
    write_jsonl(str(answers_path), answer_rows)
    write_jsonl(str(pred_path), pred_rows)
    log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    print(f"[replay] 完成: 已生成")
    print(f"            {answers_path}")
    print(f"            {pred_path}")
    print(f"            {log_path}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_argparse() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="replay_harness",
        description="因果层 PoC 会话回放 harness(调度 + 输出契约, 不含抽取/因果/问答逻辑)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="回放样本并对默认/指定题库生成答案与 predictions")
    run.add_argument("samples", help="samples.jsonl: {id, ts, content, kind}")
    run.add_argument("--out", required=True, help="输出目录(answers.jsonl/predictions.jsonl/replay_log.txt)")
    run.add_argument("--plugin", default=None, help="管线插件 .py 文件(按类名匹配 Pipeline 子类)")
    run.add_argument("--plugin-class", default=None, help="插件中 Pipeline 子类的类名(模块里有多个时必填)")
    run.add_argument("--questions", default=None, help="题库 jsonl,覆盖默认 13 道题库")
    run.add_argument("--dry-run", action="store_true", help="仅解析并打印任务描述,不执行回放")
    run.set_defaults(func=cmd_run)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_argparse()
    args = parser.parse_args(argv)

    if getattr(args, "dry_run", False):
        print(f"[dry-run] samples={args.samples} out={args.out} "
              f"plugin={args.plugin or 'StubPipeline'} questions={args.questions or 'default'}")
        return 0

    if args.command == "run":
        return cmd_run(args)
    parser.error(f"未知命令: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
