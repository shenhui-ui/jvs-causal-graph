#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory_eval.py —— 个人工作助手「记忆层」评测工具(纯标准库,不引入第三方依赖,不调用任何 LLM)

=========================================================================
一、定位
=========================================================================
本工具负责把记忆层评测集(C01 / C01b / C02)的 Markdown 源文件解析成结构化
JSON Lines,并按预测记录计算指标、输出汇总报告。它只做「规则化 / 结构化」的
判分与人机复核清单生成,**不做语义级判断**——真正的答案语义正确性需要接入真实的
召回/纠错管线(见 `--plugin` 与 README 的「接入真实管线」章节)。

支持三个评测集,对应三种 schema:

  * qa     —— C01 跨时间线问答(事实 / 因果更新 / 时间衰减 / 纠错)
  * causal —— C01b 因果扩展集(A 部分:因果题 R-Cxx;B 部分:反事实题 CF-xx)
  * error  —— C02 错误记忆基准(FT/ST/DP/AM/CT 五类)

=========================================================================
二、schema 字段定义(convert 输出 / score 读取所使用)
=========================================================================
统一约定:convert 输出的每条记录都带有一个 `schema` 字段,标明来源评测集;
字段名一律使用折线小写驼峰(lowerCamel)。无法解析的行会写入标准错误并计数,
但**不会中断**整个转换流程。

[qa / causal 通用字段]
  id           qid 唯一编号(必填)。qa 形如 F01/C01/T01/R01;causal 形如 R-C01 / CF-01。
  type         题型。qa:事实 / 因果更新 / 时间衰减 / 纠错。causal:因果 / 反事实(一般由 id 推断)。
  question     问题(必填)。
  answer       标准答案(必填)。
  source       源材料(可选)。
  evidence     证据句(可选;causal A 部分用)。
  source_ref   信息出处(可选)。
  difficulty   难度 低/中/高(可选)。
  distractors  迷惑项(可选)。
  note         备注(可选)。
  related      对应变更(可选;causal 部分用:「对应 C01 编号」/「对应变更」)。
  basis        依据(可选;causal B 部分用)。
  confidence   置信度 中/低(可选;causal B 部分用)。
  part         causal 专用:A(因果题 R-Cxx)或 B(反事实题 CF-xx)。由 id 前缀推断。

[error 专用字段]
  id           编号(必填)。形如 FT-01 / ST-01 / DP-01 / AM-01 / CT-01。
  type         错误记忆类型 FT/ST/DP/AM/CT(可选,可由 id 前缀推断)。
  input        原始对话输入(必填)。
  wrong_mem    错误记忆(必填)。
  expect_action 期望修正动作(必填)。可组合:删除 / 降级为待确认 / 合并为一条 /
               按时间更新 / 补全信息 等,用 、、; 、/ 、| 分隔。
  correct_mem  修正后的正确记忆(可选;用于过纠率判定)。
  difficulty   难度(可选)。
  checkpoint   检验点(可选)。

=========================================================================
三、命令摘要
=========================================================================
  python memory_eval.py convert <md...> --schema qa|causal|error --out <jsonl>
  python memory_eval.py score <predictions.jsonl> --schema qa|causal|error [--gold <jsonl>] [--plugin <py>] [--match-threshold 0.4]
  python memory_eval.py report <scores.json> [--out <report.md>]
  python memory_eval.py dryrun --gold <jsonl> --schema qa|causal|error --out <template.jsonl>

说明:score 会优先使用每条预测记录里自带的 gold 字段(standard_answer 等);
若记录缺少这些字段,再通过 `--gold`(convert 生成的 jsonl)按 id 补齐。
"""

import argparse
import inspect
import importlib.util
import json
import os
import re
import sys
from abc import ABC, abstractmethod
from datetime import datetime, timezone

__version__ = "1.0.0"

# ---------------------------------------------------------------------------
# 基础常量与配置
# ---------------------------------------------------------------------------

# 支持的命令行 schema
SCHEMAS = ("qa", "causal", "error")

# 反事实题 id 前缀(用于区分 causal 的 A/B 部分)
_CF_PREFIX = "CF"
_RC_PREFIX = "R-C"

# 动作词表(C02 期望修正动作);用于把动作字符串拆成规范化集合
ACTION_VOCAB = (
    "删除",
    "降级为待确认",
    "合并为一条",
    "按时间更新",
    "补全信息",
)

# 字段别名映射:规范化后的字段名(logical label)→ 统一字段名。
# 同一字段可有多种写法(不同评测集的格式略有差异),全部归并到一个规范名字。
QA_ALIASES = {
    "qid": "id", "id": "id", "编号": "id", "序号": "id",
    "题型": "type", "类型": "type", "类别": "type",
    "源材料": "source", "材料": "source", "原文": "source", "原始": "source",
    "问题": "question", "提问": "question", "题干": "question",
    "标准答案": "answer", "答案": "answer", "参考答案": "answer", "参考": "answer",
    "证据句": "evidence", "证据": "evidence",
    "信息出处": "source_ref", "出处": "source_ref", "来源": "source_ref", "信息出处/依据": "source_ref",
    "难度": "difficulty",
    "迷惑项": "distractors", "干扰项": "distractors", "诱饵": "distractors",
    "备注": "note", "备注/说明": "note", "说明": "note",
    "对应c01编号": "related", "对应c01": "related", "对应c01编号/对应变更": "related",
    "对应变更": "related", "相关变更": "related", "对应变更编号": "related",
    "依据": "basis", "依据/事实层": "basis", "依据事实层": "basis", "推依据": "basis",
    "置信度": "confidence", "可信度": "confidence", "答案可信度": "confidence",
}

# causal 与 qa 共用同一套字段(含 related/basis/confidence)
CAUSAL_ALIASES = dict(QA_ALIASES)

ERROR_ALIASES = {
    "id": "id", "编号": "id", "序号": "id",
    "类型": "type", "类别": "type",
    "原始对话输入": "input", "原始对话": "input", "原始输入": "input",
    "对话输入": "input", "原始对话input": "input",
    "错误记忆": "wrong_mem", "错误记忆内容": "wrong_mem", "错误内容": "wrong_mem",
    "期望修正动作": "expect_action", "期望动作": "expect_action", "修正动作": "expect_action",
    "期望修正动作/动作": "expect_action",
    "修正后的正确记忆": "correct_mem", "修正后记忆": "correct_mem",
    "正确记忆": "correct_mem", "修正后的正确记忆/内容": "correct_mem",
    "难度": "difficulty",
    "检验点": "checkpoint", "检查点": "checkpoint",
}

# 每种 schema 的:id 匹配规则、字段别名、必填字段
SCHEMA_CONFIG = {
    "qa": {
        "id_pattern": re.compile(r"^(?:F|C|T|R)-?\d{1,4}$"),
        "aliases": QA_ALIASES,
        "required": ("id", "question", "answer"),
    },
    "causal": {
        "id_pattern": re.compile(r"^(?:R-C\d{1,4}|CF-\d{1,4})$"),
        "aliases": CAUSAL_ALIASES,
        "required": ("id", "question", "answer"),
    },
    "error": {
        "id_pattern": re.compile(r"^(?:FT|ST|DP|AM|CT)-?\d{1,4}$"),
        "aliases": ERROR_ALIASES,
        "required": ("id", "input", "wrong_mem", "expect_action"),
    },
}

# 常见英文标点 + 中文标点,用于归一化时剔除
_PUNCT_RE = re.compile(r"[\u3000\s，。！？、；：·…—–（）()《》〈〉【】\[\]「」『』“”\"'\u2018\u2019\u201c\u201d.,;:!?/\\|+*~`^_=<>]{1,}")

# 匹配一行行首「候选 id」的宽泛模式:字母开头,可含连字符与数字
_ID_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]*\d{1,4}")


# ---------------------------------------------------------------------------
# 通用工具函数
# ---------------------------------------------------------------------------

def strip_md(text):
    """去除 markdown 标记(加粗 `**`,行内代码 `` ` ``,块引用行首 `>`)并去掉首尾空白。"""
    if not text:
        return ""
    t = re.sub(r"\*\*", "", text)
    t = re.sub(r"`", "", t)
    t = re.sub(r"(?m)^>\s?", "", t)  # 块引用
    return t.strip()


def norm_label(label):
    """规范化字段标签:去 markdown、去空白、统一小写,用于与别名表比对。"""
    if not label:
        return ""
    t = label.replace("**", "").replace("`", "")
    t = re.sub(r"\s+", "", t)
    t = t.strip("：:")
    return t.lower()


def normalize(text):
    """归一化文本用于相似度/命中判断:去标记,去标点与空白,统一小写。"""
    t = strip_md(text)
    t = t.lower()
    t = re.sub(_PUNCT_RE, "", t)
    return t


def _bigrams(s):
    """生成归一化字符串的二元组集合(CJK 下的相似度 proxy)。"""
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def bigram_sim(a, b):
    """字符二元组 Jaccard 相似度,范围 [0,1]。"""
    A = _bigrams(normalize(a))
    B = _bigrams(normalize(b))
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def texts_match(pred, gold, threshold=0.4):
    """判断预测答案是否命中标准答案(启发式,非语义)。

    命中条件(任一满足):
      1) 归一化后完全相等;
      2) 一方包含另一方(子串);
      3) 字符二元组 Jaccard 相似度 >= threshold。
    """
    p = normalize(pred)
    g = normalize(gold)
    if not p or not g:
        return False
    if p == g:
        return True
    if p in g or g in p:
        return True
    return bigram_sim(p, g) >= threshold


def parse_actions(text_or_list):
    """把「期望/预测修正动作」解析成规范化集合。

    支持：
      * 传入列表([ '删除', '按时间更新' ]);
      * 传入字符串,用 、 , ， / | ; ； 空格 分隔;
      * 自动剔除「可组合」「组合」这类元信息词与空串。
    """
    if text_or_list is None:
        return set()
    if isinstance(text_or_list, list):
        raw = list(text_or_list)
    else:
        text = str(text_or_list)
        # 若整体像一条动作(不含分隔符)直接作为一项
        raw = re.split(r"[、,，/|;；\s]+", text)
    tokens = set()
    for r in raw:
        if r is None:
            continue
        s = normalize(r)
        if not s:
            continue
        if s in ("可组合", "组合"):
            continue
        tokens.add(s)
    return tokens


def infer_id_prefix(schema, qid):
    """根据 schema 与 qid 推断题型/类型/部分,作为缺失字段的兜底。"""
    qid = (qid or "").upper()
    if schema == "causal":
        if qid.startswith(_RC_PREFIX):
            return {"type": "因果", "part": "A"}
        if qid.startswith(_CF_PREFIX):
            return {"type": "反事实", "part": "B"}
        return {}
    if schema == "qa":
        m = {"F": "事实", "C": "因果更新", "T": "时间衰减", "R": "纠错"}
        if qid and qid[0] in m:
            return {"type": m[qid[0]]}
        return {}
    if schema == "error":
        m = {"FT": "FT", "ST": "ST", "DP": "DP", "AM": "AM", "CT": "CT"}
        head = qid.split("-")[0]
        if head in m:
            return {"type": m[head]}
        return {}
    return {}


def log_warn(message):
    """把警告输出到标准错误,便于与主输出(jsonl/markdown)分离。"""
    sys.stderr.write("[memory_eval][warn] {0}\n".format(message))


def ensure_parent(path):
    """确保输出文件的父目录存在。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def read_jsonl(path):
    """读取 JSONL 文件,返回(records, errors)。逐行容错,坏行不中断。"""
    records, errors = [], []
    if not os.path.exists(path):
        raise ValueError("找不到文件: {0}".format(path))
    with open(path, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    records.append(obj)
                else:
                    errors.append((i, "非对象类型: {0}".format(type(obj).__name__)))
            except json.JSONDecodeError as exc:
                errors.append((i, "JSON 解析失败: {0}".format(exc)))
    return records, errors


def write_jsonl(records, path):
    """把记录列表写入 JSONL(GZIP 无关;纯文本,UTF-8)。"""
    ensure_parent(path)
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Markdown 解析
# ---------------------------------------------------------------------------

def _leading_id_token(text):
    """去掉行首的 markdown 装饰(`,``*,列表符号等)后,若开头恰为一个 id token 则返回之。

    关键要求:**id 必须是行/标题的第一个 token**,否则像 `A 部分 · 因果题 R-C01 ~ R-C12`
    这样的章节小标题(其 id 只是修饰语)不会被误判为条目起点。
    """
    if not text:
        return None
    t = text.lstrip()
    while True:
        m = re.match(r"(?:\*\*|`|[-*+]\s+)", t)
        if m:
            t = t[m.end():].lstrip()
        else:
            break
    m = re.match(r"([A-Za-z][A-Za-z0-9-]*\d{1,4})", t)
    if m:
        return m.group(1)
    return None


def boundary_kind(line, id_pattern):
    """判断某一行是否为「条目边界」,返回三类之一:

      ('new_heading', id)  —— 是条目标题行(如 `## R-C01 ...`),后继行作为该条目正文;
      ('new_inline',  id)  —— 是「一条一行式」的紧凑条目(整行即条目),需立即解析;
      ('close', None)      —— 是普通章节标题行(如 `## 一、字段说明`),关闭当前条目;
      None                 —— 普通行,归属当前条目正文。
    """
    stripped = line.lstrip()
    m = re.match(r"^(#{1,6})[ \t]+(.*)$", stripped)
    if m:
        level = len(m.group(1))
        text = m.group(2).strip()
        cand = _leading_id_token(text)
        # 仅二级(##)及以上标题才可能成为条目起点:一级标题(# 标题名)如 `# C01 ...`
        # 只描述文档名,不应与条目 id 混淆。
        if cand and level >= 2 and id_pattern.fullmatch(cand):
            return ("new_heading", cand)
        return ("close", None)

    # 非标题行:若行首恰为一个 id → 紧凑一条一行式条目
    cand = _leading_id_token(line)
    if cand and id_pattern.fullmatch(cand):
        return ("new_inline", cand)
    return None


def _split_field_label(line):
    """从一行中尝试切出(label, rest)。

    匹配形如 `- **qid**：R-C01` 的字段行;若字段名不在别名表内则返回 None,
    以避免把正文续行误判为字段。
    """
    m = re.match(
        r"^\s*(?:[-*+]\s*)?(?:\*\*)?(?P<label>[^：:\n]{1,40}?)(?:\*\*)?\s*[：:]\s*(?P<rest>.*)$",
        line,
    )
    if not m:
        return None
    label = norm_label(m.group("label"))
    rest = m.group("rest")
    return (label, rest) if label else None


def _clean_value(lines):
    """把若干行值行合并成一个字符串:去空白/去 `>`/去行首破折号,去掉首尾空行。"""
    out = []
    for ln in lines:
        t = re.sub(r"^\s*>\s?", "", ln)
        t = re.sub(r"^\s*[-*+]\s+", "", t)
        t = t.strip()
        if t:
            out.append(t)
    return "\n".join(out)


def extract_structured(lines, alias_map):
    """按「每条字段占一行(可为多行块引号值)」的格式抽取字段。"""
    fields = {}
    cur = None
    cur_lines = []

    def flush():
        nonlocal cur, cur_lines
        if cur is not None:
            fields[cur] = _clean_value(cur_lines)
        cur, cur_lines = None, []

    for line in lines:
        info = _split_field_label(line)
        if info:
            label, rest = info
            canon = alias_map.get(label)
            if canon:
                flush()
                cur = canon
                cur_lines = [rest] if rest.strip() else []
                continue
        # 非字段行 → 作为当前字段的续行
        if cur is not None:
            if re.match(r"^\s*>", line) and cur not in ("source", "input", "source_ref"):
                # 非材料类字段遇到块引用 → 视为注释直接跳过
                continue
            if line.strip():
                cur_lines.append(line)
    flush()
    return fields


def extract_compact(text, alias_map):
    """按「一条一行式:``F01``(事实/低):源材料:…。问题:…标准答案:…」抽取字段。"""
    labels = sorted({k for k in alias_map if k}, key=len, reverse=True)
    if not labels:
        return None
    alt = "|".join(re.escape(l) for l in labels)
    pat = re.compile(r"(?<![A-Za-z0-9])(?P<label>" + alt + r")\s*[：:]")
    matches = list(pat.finditer(text))
    if not matches:
        return None
    fields = {}
    for i, m in enumerate(matches):
        canon = alias_map.get(norm_label(m.group("label")))
        if canon is None:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        # 一条一行式以标点分隔字段,值末尾常粘连「。;?」等边界符,将其剥掉
        val = re.sub(r"(?s)[\u3002\uff0e\uff1b;\uff01\uff1f!?]+$", "", text[start:end]).strip()
        piece = _clean_value([val])
        if canon not in fields:
            fields[canon] = piece
        else:
            fields[canon] = (fields[canon] + "\n" + piece).strip()
    head = strip_md(text[: matches[0].start()])
    m2 = _ID_TOKEN_RE.search(head)
    if m2 and "id" not in fields:
        fields["id"] = m2.group(0)
    return fields


def _build_item(qid, lines, cfg, filename):
    """把「标题 + 正文行」构造成一条记录;失败返回 None。"""
    alias_map = cfg["aliases"]
    schema = None
    for s, c in SCHEMA_CONFIG.items():
        if c is cfg:
            schema = s
            break
    fields = extract_structured(lines, alias_map)
    if not all(k in fields for k in cfg["required"]):
        # 结构化失败则尝试把整个一体文本当作紧凑格式
        compact = extract_compact("\n".join(lines), alias_map)
        if compact:
            merged = dict(compact)
            merged.update({k: v for k, v in fields.items() if v})  # 结构化更可靠部分优先
            fields = merged
    if not all(k in fields for k in cfg["required"]):
        return None, "缺少必填字段(需要 {0}),解析到字段:{1}".format(
            "/".join(cfg["required"]), ",".join(sorted(fields) or ["无"])
        )
    if not fields.get("id"):
        fields["id"] = qid
    fields["schema"] = schema
    fields["source_file"] = filename
    # 缺失字段按 schema 从 id 兜底
    inferred = infer_id_prefix(schema, fields.get("id"))
    for k, v in inferred.items():
        fields.setdefault(k, v)
    return fields, None


def _build_inline(line, cfg, filename):
    """解析一条紧凑行(一条一行式条目)。"""
    alias_map = cfg["aliases"]
    schema = None
    for s, c in SCHEMA_CONFIG.items():
        if c is cfg:
            schema = s
            break
    fields = extract_compact(line, alias_map)
    if not fields or not all(k in fields for k in cfg["required"]):
        return None, "紧凑行解析失败(需要必填:{0})".format(
            "/".join(cfg["required"])
        )
    fields["schema"] = schema
    fields["source_file"] = filename
    inferred = infer_id_prefix(schema, fields.get("id"))
    for k, v in inferred.items():
        fields.setdefault(k, v)
    return fields, None


def parse_markdown(text, schema, filename):
    """解析整份 Markdown,返回 (items, errors)。错误只计数不中断。"""
    cfg = SCHEMA_CONFIG[schema]
    lines = text.splitlines()
    items, errors = [], []
    cur = None

    def flush_current():
        nonlocal cur
        if cur is not None:
            item, err = _build_item(cur["id"], cur["lines"], cfg, filename)
            if item:
                items.append(item)
            elif err:
                errors.append((cur.get("line", 0), err))
            cur = None

    for ln_no, line in enumerate(lines, 1):
        res = boundary_kind(line, cfg["id_pattern"])
        if res is None:
            if cur is not None:
                cur["lines"].append(line)
        elif res[0] == "new_heading":
            flush_current()
            cur = {"id": res[1], "lines": [], "line": ln_no}
        elif res[0] == "new_inline":
            flush_current()
            item, err = _build_inline(line, cfg, filename)
            if item:
                items.append(item)
            elif err:
                errors.append((ln_no, err))
        elif res[0] == "close":
            flush_current()
    flush_current()
    return items, errors


def cmd_convert(argv):
    """convert 子命令:把 Markdown 解析为 JSON Lines。"""
    p = argparse.ArgumentParser(
        prog="memory_eval convert",
        description="把评测集 Markdown 解析为 JSON Lines(无法解析的行会警告并计数,不中断)。",
    )
    p.add_argument("md_files", nargs="+", help="一个或多个 Markdown 源文件")
    p.add_argument("--schema", required=True, choices=SCHEMAS, help="评测集 schema")
    p.add_argument("--out", required=True, help="输出 JSON Lines 路径")
    args = p.parse_args(argv)

    all_items, all_errors = [], []
    for path in args.md_files:
        if not os.path.exists(path):
            log_warn("跳过不存在的文件: {0}".format(path))
            continue
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        items, errors = parse_markdown(text, args.schema, os.path.basename(path))
        all_items.extend(items)
        all_errors.extend(errors)

    write_jsonl(all_items, args.out)
    if all_errors:
        for ln_no, err in all_errors:
            log_warn("第 {0} 行无法解析: {1}".format(ln_no, err))

    print(
        "[memory_eval] convert 完成:解析 {0} 条,警告 {1} 条,输出 {2}(schema={3})".format(
            len(all_items), len(all_errors), args.out, args.schema
        )
    )
    return 0


# ---------------------------------------------------------------------------
# 打分 / 判分
# ---------------------------------------------------------------------------

def is_causal_item(record, schema):
    """判断该记录是否为「因果类」题目(用于结构判定)。"""
    if schema == "causal":
        pid = record.get("part") or infer_id_prefix("causal", record.get("id")).get("part")
        return pid == "A"
    t = record.get("type") or ""
    return t in ("因果", "因果更新")


def is_counterfactual_item(record, schema):
    """判断该记录是否为「反事实(推断)」题目(不出自动分,走人工清单)。"""
    if schema == "causal":
        pid = record.get("part") or infer_id_prefix("causal", record.get("id")).get("part")
        return pid == "B"
    t = record.get("type") or ""
    if "反事实" in t or "推断" in t:
        return True
    qid = (record.get("id") or "").upper()
    return qid.startswith(_CF_PREFIX)


def _as_answer_list(record):
    """从预测记录中取出排好序的候选答案列表。"""
    answers = record.get("answers")
    if isinstance(answers, list):
        ans = [a for a in answers if str(a or "").strip()]
        if ans:
            return ans
    single = record.get("predicted_answer")
    if isinstance(single, list):
        single = [s for s in single if str(s or "").strip()]
        return single or []
    if single is None:
        return []
    return [str(single)] if str(single).strip() else []


def eval_qa_item(record, gold, schema, threshold):
    """对单条 qa / causal-A 记录打分。"""
    answers = _as_answer_list(record)
    gold_answer = (gold.get("answer") or record.get("standard_answer") or "").strip()

    evaluated = len(answers) > 0 and bool(gold_answer)
    top1 = bool(answers) and texts_match(answers[0], gold_answer, threshold)
    top3 = any(texts_match(a, gold_answer, threshold) for a in answers[:3]) if answers else False

    out = {
        "id": record.get("id"),
        "type": record.get("type") or gold.get("type") or "",
        "difficulty": record.get("difficulty") or gold.get("difficulty") or "",
        "gold_answer": gold_answer,
        "predicted_answers": answers,
        "top1_hit": bool(top1),
        "top3_hit": bool(top3),
        "evaluated": evaluated,
        "note": "",
    }

    # 因果题结构判定:答案需含「当时 / 后来」双时点(规则化启发式)
    if is_causal_item(record, schema):
        cand = answers[0] if answers else ""
        struct_pass = ("当时" in cand) and ("后来" in cand)
        out["structure_checked"] = True
        out["structure_pass"] = bool(struct_pass)
    else:
        out["structure_checked"] = False
        out["structure_pass"] = None
    return out


def eval_counterfactual_item(record, gold):
    """反事实题不做自动判分,生成「人工判分清单」条目(带自动预检三要素)。"""
    answers = _as_answer_list(record)
    text = answers[0] if answers else (record.get("predicted_answer") or "")
    text = str(text)
    gold_answer = (gold.get("answer") or record.get("standard_answer") or "").strip()

    # 三要素自动预检(供人工勾选参考;最终判定由人工完成)
    marked_inference = ("推断" in text) or ("非事实层" in text)
    refs_fact_layer = ("事实层" in text) or bool(re.search(r"C0\d", text)) or ("依据" in text)
    gives_confidence = ("置信度" in text) or ("可信度" in text)

    return {
        "id": record.get("id"),
        "type": record.get("type") or gold.get("type") or "反事实",
        "difficulty": record.get("difficulty") or gold.get("difficulty") or "高",
        "gold_answer": gold_answer,
        "predicted_answer": text,
        "auto_marked_inference": bool(marked_inference),
        "auto_refs_fact_layer": bool(refs_fact_layer),
        "auto_gives_confidence": bool(gives_confidence),
        "human_marked_inference": record.get("human_marked_inference"),
        "human_refs_fact_layer": record.get("human_refs_fact_layer"),
        "human_gives_confidence": record.get("human_gives_confidence"),
        "human_note": record.get("human_note", ""),
    }


def eval_error_item(record, gold):
    """对单条 C02 记录打分:修正动作匹配率 + 过纠率。"""
    expected = parse_actions(gold.get("expect_action") or record.get("expect_action"))
    predicted = parse_actions(record.get("predicted_actions") or record.get("predicted_action"))

    gold_mem = (gold.get("correct_mem") or record.get("correct_mem") or "").strip()
    pred_mem = (record.get("predicted_corrected_memory") or "").strip()

    matched = (expected & predicted) if expected else set()
    if expected:
        match_rate = len(matched) / len(expected)
        precision = (len(matched) / len(predicted)) if predicted else 0.0
        exact_all = predicted == expected
        extra = predicted - expected
    else:
        match_rate = None
        precision = None
        exact_all = None
        extra = set()

    sim = bigram_sim(pred_mem, gold_mem) if (pred_mem and gold_mem) else None
    # 过纠判定:
    #   (1) 预测动作超出了期望动作集合(结构性过纠);
    #   (2) 已覆盖期望动作,但「修正后的正确记忆」与黄金正确记忆相似度过低(把正确记忆改错)。
    content_over = (
        bool(pred_mem and gold_mem) and sim is not None and sim < 0.5
    )
    over_correction = bool(extra) or bool(content_over)

    return {
        "id": record.get("id"),
        "type": record.get("type") or gold.get("type") or "",
        "difficulty": record.get("difficulty") or gold.get("difficulty") or "",
        "expected_actions": sorted(expected),
        "predicted_actions": sorted(predicted),
        "matched_actions": sorted(matched),
        "action_match_rate": match_rate,
        "action_precision": precision,
        "exact_action_all": exact_all,
        "extra_actions": sorted(extra),
        "correct_mem": gold_mem,
        "predicted_corrected_memory": pred_mem,
        "memory_similarity": sim,
        "over_correction": bool(over_correction),
    }


def score_schema(records, gold_map, schema, threshold):
    """对整批预测记录按 schema 打分,返回 scores 字典。"""
    gold_by_id = gold_map
    n = len(records)

    if schema in ("qa", "causal"):
        qa_items, qa_evaluated, qa_top1, qa_top3 = [], 0, 0, 0
        cf_items, cf_marked, cf_ref, cf_conf = [], 0, 0, 0
        causal_items, causal_pass = [], 0
        type_tot = {}
        diff_tot = {}

        def note3(d):
            return [d.get("k") for k in ("n", "top1", "top3")]

        for rec in records:
            rec_id = rec.get("id")
            gold = gold_by_id.get(rec_id, {})
            merged = dict(gold)
            merged.update({k: v for k, v in rec.items() if v not in (None, "", [])})
            merged["id"] = rec_id

            if is_counterfactual_item(merged, schema):
                cf = eval_counterfactual_item(rec, gold)
                cf_items.append(cf)
                cf_marked += 1 if cf["auto_marked_inference"] else 0
                cf_ref += 1 if cf["auto_refs_fact_layer"] else 0
                cf_conf += 1 if cf["auto_gives_confidence"] else 0
                continue

            ev = eval_qa_item(merged, gold, schema, threshold)
            qa_items.append(ev)
            if ev["evaluated"]:
                qa_evaluated += 1
                qa_top1 += 1 if ev["top1_hit"] else 0
                qa_top3 += 1 if ev["top3_hit"] else 0
            if ev.get("structure_checked"):
                causal_items.append(ev["id"])
                causal_pass += 1 if ev["structure_pass"] else 0

            t = ev["type"] or "未标注"
            d = ev["difficulty"] or "未标注"
            for bucket, key in ((type_tot, t), (diff_tot, d)):
                b = bucket.setdefault(key, {"n": 0, "top1": 0, "top3": 0})
                if ev["evaluated"]:
                    b["n"] += 1
                    b["top1"] += 1 if ev["top1_hit"] else 0
                    b["top3"] += 1 if ev["top3_hit"] else 0

        top1_rate = qa_top1 / qa_evaluated if qa_evaluated else 0.0
        top3_rate = qa_top3 / qa_evaluated if qa_evaluated else 0.0
        structure_rate = causal_pass / len(causal_items) if causal_items else None

        summary = {
            "schema": schema,
            "n_predictions": n,
            "n_auto_scored": len(qa_items),
            "n_evaluated": qa_evaluated,
            "top1_hit": qa_top1,
            "top3_hit": qa_top3,
            "top1_hit_rate": top1_rate,
            "top3_hit_rate": top3_rate,
            "breakdown_by_type": type_tot,
            "breakdown_by_difficulty": diff_tot,
            "causal_structure": {
                "n_checked": len(causal_items),
                "n_pass": causal_pass,
                "structure_pass_rate": structure_rate,
            },
            "counterfactual": {
                "n": len(cf_items),
                "auto_marked_inference": cf_marked,
                "auto_refs_fact_layer": cf_ref,
                "auto_gives_confidence": cf_conf,
            },
        }
        return {"schema": schema, "summary": summary, "items": qa_items,
                "counterfactual_items": cf_items}

    if schema == "error":
        err_items, rates, exact_all, over = [], [], 0, 0
        for rec in records:
            rec_id = rec.get("id")
            gold = gold_by_id.get(rec_id, {})
            ev = eval_error_item(rec, gold)
            err_items.append(ev)
            if ev["action_match_rate"] is not None:
                rates.append(ev["action_match_rate"])
            if ev["exact_action_all"]:
                exact_all += 1
            if ev["over_correction"]:
                over += 1
        n_rate = len(rates)
        summary = {
            "schema": schema,
            "n_predictions": n,
            "n_scored": n_rate,
            "action_match_rate": (sum(rates) / n_rate) if n_rate else None,
            "exact_action_all_rate": (exact_all / n_rate) if n_rate else None,
            "over_correction_rate": (over / n_rate) if n_rate else None,
            "n_exact": exact_all,
            "n_over_correction": over,
        }
        return {"schema": schema, "summary": summary, "items": err_items,
                "counterfactual_items": []}

    raise ValueError("未知 schema: {0}".format(schema))


def cmd_score(argv):
    """score 子命令:按预测记录计算指标。"""
    p = argparse.ArgumentParser(
        prog="memory_eval score",
        description="按预测记录计算指标(可 --plugin 接入真实召回/纠错管线)。",
    )
    p.add_argument("predictions", nargs="?",
                   help="预测 JSONL(可由 dryrun 生成模板后填写;使用 --plugin 时可省略)")
    p.add_argument("--schema", choices=SCHEMAS, default=None, help="评测集 schema")
    p.add_argument("--gold", default=None, help="convert 生成的黄金 JSONL(按 id 补齐字段)")
    p.add_argument("--plugin", default=None, help="可选 Python 插件路径(含 Recaller / Fixer 子类)")
    p.add_argument("--recaller-class", default=None, help="插件里用于召回的类名(默认自动探测)")
    p.add_argument("--fixer-class", default=None, help="插件里用于纠错的类名(默认自动探测)")
    p.add_argument("--match-threshold", type=float, default=0.4, help="命中相似度阈值(默认 0.4)")
    p.add_argument("--out", default=None, help="评分 JSON 输出路径(默认打印到 stdout)")
    args = p.parse_args(argv)

    schema = args.schema
    # 预测来源:support 两种情况——(1) 直接给定预测 JSONL;(2) 通过 --plugin + --gold 现场生成。
    records, read_errors = [], []
    if args.predictions:
        records, read_errors = read_jsonl(args.predictions)
        for ln_no, err in read_errors:
            log_warn("预测文件第 {0} 行解析失败: {1}".format(ln_no, err))
    elif not args.plugin:
        log_warn("缺少预测输入:请提供 <predictions> 文件,或使用 --plugin + --gold 现场生成。")
        return 1

    gold_map = {}
    if args.gold:
        gold_recs, gerr = read_jsonl(args.gold)
        for ln_no, err in gerr:
            log_warn("黄金文件第 {0} 行解析失败: {1}".format(ln_no, err))
        for g in gold_recs:
            if g.get("id"):
                gold_map[str(g.get("id"))] = g
        if schema is None:
            # 从黄金记录推断 schema
            for g in gold_recs:
                if g.get("schema") in SCHEMAS:
                    schema = g["schema"]
                    break
    elif schema is None:
        # 从第一条预测记录中的 gold 字段推断 schema
        for r in records:
            if r.get("schema") in SCHEMAS:
                schema = r["schema"]
                break
    if schema is None:
        log_warn("无法推断 schema;请显式传入 --schema。")
        schema = "qa"

    # 若接入插件,则由插件按黄金条目生成预测
    if args.plugin:
        if not args.gold:
            log_warn("使用 --plugin 需要同时提供 --gold(黄金条目)。")
            return 1
        records = plugin_generate_predictions(
            args.plugin, gold_map, schema, args.recaller_class, args.fixer_class
        )

    scores = score_schema(records, gold_map, schema, args.match_threshold)
    meta = {
        "tool": "memory_eval",
        "version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema": schema,
        "match_threshold": args.match_threshold,
        "n_predictions": len(records),
        "missing_gold_ids": [r.get("id") for r in records if str(r.get("id")) not in gold_map] if gold_map else [],
    }
    scores["meta"] = meta

    if args.out:
        ensure_parent(args.out)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(scores, fh, ensure_ascii=False, indent=2)
        print("[memory_eval] score 完成:输出 {0}(schema={1})".format(args.out, schema))
    else:
        print(json.dumps(scores, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# report 子命令
# ---------------------------------------------------------------------------

def _fmt_pct(value, ndigits=1):
    if value is None:
        return "N/A"
    return "{0:.{1}f}%".format(value * 100, ndigits)


def cmd_report(argv):
    """report 子命令:读取 scores.json 输出 Markdown 汇总报告。"""
    p = argparse.ArgumentParser(
        prog="memory_eval report", description="把评分 JSON 渲染为 Markdown 汇总报告。"
    )
    p.add_argument("scores", help="score 命令生成的 JSON")
    p.add_argument("--out", default=None, help="Markdown 输出路径(默认打印到 stdout)")
    args = p.parse_args(argv)

    with open(args.scores, "r", encoding="utf-8") as fh:
        scores = json.load(fh)

    schema = scores.get("schema") or scores.get("meta", {}).get("schema") or "?"
    meta = scores.get("meta", {})
    summary = scores.get("summary", {})
    lines = []
    lines.append("# 记忆层评测汇总报告")
    lines.append("")
    lines.append("- **schema**: {0}".format(schema))
    lines.append("- **工具版本**: {0}".format(meta.get("version", "?")))
    lines.append("- **生成时间**: {0}".format(meta.get("generated_at", "?")))
    lines.append("- **预测条数**: {0}".format(meta.get("n_predictions", summary.get("n_predictions", 0))))
    lines.append("- **匹配阈值**: {0}".format(meta.get("match_threshold", "-")))
    lines.append("")

    if schema in ("qa", "causal"):
        n_eval = summary.get("n_evaluated", 0)
        lines.append("## 总体命中率(QA)")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("|---|---|")
        lines.append("| 可判分条目 | {0} |".format(n_eval))
        lines.append("| Top-1 命中数 | {0} |".format(summary.get("top1_hit", 0)))
        lines.append("| Top-3 命中数 | {0} |".format(summary.get("top3_hit", 0)))
        lines.append("| Top-1 命中率 | {0} |".format(_fmt_pct(summary.get("top1_hit_rate"))))
        lines.append("| Top-3 命中率 | {0} |".format(_fmt_pct(summary.get("top3_hit_rate"))))
        lines.append("")

        for title, bucket in (
            ("按题型分拆", summary.get("breakdown_by_type", {})),
            ("按难度分拆", summary.get("breakdown_by_difficulty", {})),
        ):
            lines.append("### {0}".format(title))
            lines.append("")
            if bucket:
                lines.append("| 分组 | 条目 | Top-1 | Top-3 | Top-1率 |")
                lines.append("|---|---|---|---|---|")
                for k, v in bucket.items():
                    n = v.get("n", 0)
                    t1 = v.get("top1", 0)
                    t3 = v.get("top3", 0)
                    lines.append("| {0} | {1} | {2} | {3} | {4} |".format(
                        k, n, t1, t3, _fmt_pct(t1 / n if n else None)))
                lines.append("")
            else:
                lines.append("(无条目)")
                lines.append("")

        cs = summary.get("causal_structure", {})
        lines.append("### 因果题结构判定(答案需含「当时/后来」)")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("|---|---|")
        lines.append("| 判定条数 | {0} |".format(cs.get("n_checked", 0)))
        lines.append("| 通过条数 | {0} |".format(cs.get("n_pass", 0)))
        lines.append("| 通过率 | {0} |".format(_fmt_pct(cs.get("structure_pass_rate"))))
        lines.append("")

        cf = summary.get("counterfactual", {})
        lines.append("### 反话题(人工判分清单 · 自动预检)")
        lines.append("")
        lines.append("| 三要素 | 命中数 | 共 {0} 条 |".format(cf.get("n", 0)))
        lines.append("|---|---|---|")
        lines.append("| 是否标注推断 | {0} | |".format(cf.get("auto_marked_inference", 0)))
        lines.append("| 是否引用事实层 | {0} | |".format(cf.get("auto_refs_fact_layer", 0)))
        lines.append("| 是否给出置信度 | {0} | |".format(cf.get("auto_gives_confidence", 0)))
        lines.append("")
        lines.append("> 注:反话题不做自动判分,下列为逐条清单(自动预检仅供参考,由人工勾选/复核)。")
        lines.append("")
        cf_items = scores.get("counterfactual_items", [])
        if cf_items:
            lines.append("| qid | 是否标注推断 | 是否引用事实层 | 是否给出置信度 | 人工标注推断 | 人工引用事实层 | 人工置信度 |")
            lines.append("|---|---|---|---|---|---|---|")
            for it in cf_items:
                lines.append("| {0} | {1} | {2} | {3} | {4} | {5} | {6} |".format(
                    it.get("id"),
                    "√" if it.get("auto_marked_inference") else "×",
                    "√" if it.get("auto_refs_fact_layer") else "×",
                    "√" if it.get("auto_gives_confidence") else "×",
                    it.get("human_marked_inference") if it.get("human_marked_inference") is not None else "-",
                    it.get("human_refs_fact_layer") if it.get("human_refs_fact_layer") is not None else "-",
                    it.get("human_gives_confidence") if it.get("human_gives_confidence") is not None else "-",
                ))
            lines.append("")

    elif schema == "error":
        lines.append("## 总体指标(C02)")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("|---|---|")
        lines.append("| 可判分条目 | {0} |".format(summary.get("n_scored", 0)))
        lines.append("| 修正动作匹配率(均值) | {0} |".format(_fmt_pct(summary.get("action_match_rate"))))
        lines.append("| 动作完全匹配率 | {0} |".format(_fmt_pct(summary.get("exact_action_all_rate"))))
        lines.append("| 过纠率 | {0} |".format(_fmt_pct(summary.get("over_correction_rate"))))
        lines.append("| 完全匹配条数 | {0} |".format(summary.get("n_exact", 0)))
        lines.append("| 疑似过纠条数 | {0} |".format(summary.get("n_over_correction", 0)))
        lines.append("")

    # 要点小结
    lines.append("## 关键要点")
    lines.append("")
    lines.append("1. 自动判分为规则化启发式,**对 LLM/真实管线输出敏感**;正式结题建议接入后再人工抽检约 20%。")
    lines.append("2. 反话题(反事实)一律作为「人工判分清单」输出,不参与自动命中/正确性计分。")
    lines.append("3. 过纠率基于「动作集合超出期望 + 修正后记忆与黄金记忆相似度过低」两个信号,属结构性代理指标。")
    lines.append("")
    lines.append("> 由 `memory_eval.py` 自动生成,供人工复核使用。")

    report = "\n".join(lines)
    if args.out:
        ensure_parent(args.out)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report)
        print("[memory_eval] report 完成:输出 {0}".format(args.out))
    else:
        print(report)
    return 0


# ---------------------------------------------------------------------------
# dryrun 子命令:生成预测模板
# ---------------------------------------------------------------------------

def cmd_dryrun(argv):
    """dryrun 子命令:根据黄金 JSONL 生成空预测模板。"""
    p = argparse.ArgumentParser(
        prog="memory_eval dryrun", description="用 convert 生成的黄金 JSONL 生成空预测模板。"
    )
    p.add_argument("--gold", required=True, help="convert 生成的黄金 JSONL")
    p.add_argument("--schema", required=True, choices=SCHEMAS, help="评测集 schema")
    p.add_argument("--out", required=True, help="输出模板 JSONL")
    args = p.parse_args(argv)

    gold_recs, gerr = read_jsonl(args.gold)
    for ln_no, err in gerr:
        log_warn("黄金文件第 {0} 行解析失败: {1}".format(ln_no, err))

    template = []
    for g in gold_recs:
        if g.get("schema") not in SCHEMAS:
            g.setdefault("schema", args.schema)
        rec = {
            "id": g.get("id"),
            "schema": args.schema,
        }
        if args.schema in ("qa", "causal"):
            rec["question"] = g.get("question", "")
            rec["type"] = g.get("type", "")
            rec["difficulty"] = g.get("difficulty", "")
            rec["standard_answer"] = g.get("answer", "")
            rec["predicted_answer"] = ""
            rec["answers"] = []
            if g.get("part") == "B" or (args.schema == "causal" and str(g.get("id", "")).upper().startswith("CF")):
                rec["human_marked_inference"] = None
                rec["human_refs_fact_layer"] = None
                rec["human_gives_confidence"] = None
                rec["human_note"] = ""
        elif args.schema == "error":
            rec["type"] = g.get("type", "")
            rec["difficulty"] = g.get("difficulty", "")
            rec["input"] = g.get("input", "")
            rec["wrong_mem"] = g.get("wrong_mem", "")
            rec["expect_action"] = g.get("expect_action", "")
            rec["correct_mem"] = g.get("correct_mem", "")
            rec["predicted_actions"] = []
            rec["predicted_action"] = ""
            rec["predicted_corrected_memory"] = ""
        template.append(rec)

    write_jsonl(template, args.out)
    print("[memory_eval] dryrun 完成:生成 {0} 条模板,输出 {1}(schema={2})".format(
        len(template), args.out, args.schema))
    return 0


# ---------------------------------------------------------------------------
# 插件接口(接入真实召回 / 纠错管线)
# ---------------------------------------------------------------------------

class Recaller(ABC):
    """召回器抽象基类。M0 尚未接入时,直接实例化会抛 NotImplementedError。

    输入:一条 question 记录(dict),至少含 id/question(可含 type/difficulty 等)。
    输出:返回预测答案字符串(predicted_answer)。
    """

    @abstractmethod
    def recall(self, question):
        raise NotImplementedError(
            "M0 尚未接入召回管线:请在子类实现 Recaller.recall(question) -> str,"
            "或在 --plugin 插件中接入真实的召回/LLM 管线。"
        )


class Fixer(ABC):
    """纠错器抽象基类。M0 尚未接入时,直接实例化会抛 NotImplementedError。

    输入:一条 error 记录(dict),至少含 id/input/wrong_mem/expect_action。
    输出:返回 dict,建议包含:
        {"actions": [...], "corrected_memory": "...", "reasoning": "..."}
          -- actions: 修正动作列表(与 ACTION_VOCAB 对齐的 token);
          -- corrected_memory: 修正后的正确记忆;
          -- reasoning: 可选的处理理由。
    """

    @abstractmethod
    def fix(self, item):
        raise NotImplementedError(
            "M0 尚未接入纠错管线:请在子类实现 Fixer.fix(item) -> dict,"
            "或在 --plugin 插件中接入真实的纠错/LLM 管线。"
        )


class StubRecaller(Recaller):
    """示例桩类:明确提示 M0 尚未接入。"""

    def recall(self, question):
        raise NotImplementedError(
            "M0 尚未接入召回管线:请实现 Recaller.recall() 或提供 --plugin 实现。"
        )


class StubFixer(Fixer):
    """示例桩类:明确提示 M0 尚未接入。"""

    def fix(self, item):
        raise NotImplementedError(
            "M0 尚未接入纠错管线:请实现 Fixer.fix() 或提供 --plugin 实现。"
        )


def _is_plugin_subclass(obj, base_cls):
    """判断 obj 是否为 base_cls 的子类。

    注意:当 memory_eval 以 `python memory_eval.py` 运行时,`__main__` 与本文件
    会各自持有一份 ``Recaller``/``Fixer`` 类对象,identity 比较会失败;因此这里
    兼容「子类 MRO 中存在同名基类」的判定(名称匹配),保证插件能正常被识别。
    """
    if not inspect.isclass(obj):
        return False
    name = getattr(base_cls, "__name__", "")
    return issubclass(obj, base_cls) or (name in {c.__name__ for c in obj.__mro__})


def load_plugin_class(path, base_cls, class_name=None):
    """从指定 Python 文件中加载 base_cls 的一个子类实例。"""
    if not os.path.exists(path):
        raise ValueError("插件文件不存在: {0}".format(path))
    module_name = "memory_eval_plugin_{0}".format(os.path.splitext(os.path.basename(path))[0])
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError("无法从 {0} 载入插件".format(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # 优先使用显式指定的类名
    if class_name:
        obj = getattr(module, class_name, None)
        if obj is None or not _is_plugin_subclass(obj, base_cls):
            raise ValueError(
                "插件中找不到 {0} 子类: {1}".format(base_cls.__name__, class_name)
            )
        return obj()

    # 否则自动探测第一个「定义于本插件文件」的子类
    for name in dir(module):
        obj = getattr(module, name)
        if not _is_plugin_subclass(obj, base_cls):
            continue
        if getattr(obj, "__module__", None) != module.__name__:
            continue
        return obj()
    raise ValueError(
        "插件中没有 {0} 的子类:请实现一个 subclass 或加 --{1}-class。".format(
            base_cls.__name__,
            "recaller" if base_cls is Recaller else "fixer",
        )
    )


def plugin_generate_predictions(plugin_path, gold_map, schema, recaller_class, fixer_class):
    """使用插件按黄金条目生成预测记录。"""
    base = Recaller if schema in ("qa", "causal") else Fixer
    instance = load_plugin_class(plugin_path, base,
                                 recaller_class if schema in ("qa", "causal") else fixer_class)
    predictions = []
    for gid, gold in gold_map.items():
        if schema in ("qa", "causal"):
            plugin_input = {k: gold.get(k, "") for k in
                            ("id", "schema", "question", "type", "difficulty")}
        else:
            plugin_input = {k: gold.get(k, "") for k in
                            ("id", "schema", "input", "wrong_mem", "type", "difficulty")}
        rec = dict(plugin_input)
        try:
            if schema in ("qa", "causal"):
                if is_counterfactual_item(gold, schema):
                    out = instance.recall(plugin_input)
                    rec["predicted_answer"] = out or ""
                    rec["answers"] = [out] if out else []
                else:
                    out = instance.recall(plugin_input)
                    rec["predicted_answer"] = out or ""
                    rec["answers"] = [out] if out else []
            else:
                res = instance.fix(plugin_input)
                res = res or {}
                actions = res.get("actions") or parse_actions(res.get("action"))
                rec["predicted_actions"] = sorted(parse_actions(actions)) if actions else []
                rec["predicted_action"] = ",".join(sorted(parse_actions(actions))) if actions else ""
                rec["predicted_corrected_memory"] = res.get("corrected_memory", "") or ""
        except NotImplementedError as exc:
            log_warn("插件 {0} 未实现,条目 {1} 跳过: {2}".format(plugin_path, gid, exc))
            continue
        predictions.append(rec)
    return predictions


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="memory_eval",
        description="个人工作助手「记忆层」评测工具(纯标准库,不调用 LLM)。",
    )
    parser.add_argument("--version", action="version", version="memory_eval {0}".format(__version__))
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("convert", help="把评测集 Markdown 解析为 JSON Lines").set_defaults(func=cmd_convert)
    # 注意:convert 子命令参数在其 def 内部解析;这里仅注册以启用 --help
    cp = sub.add_parser("score", help="按预测记录计算指标").set_defaults(func=cmd_score)
    # 子命令的具体 options 由于要在模块级 argv 里多次注册,改为在命令函数里处理,因此这里保持轻量
    rp = sub.add_parser("report", help="把评分 JSON 渲染为 Markdown 汇总报告").set_defaults(func=cmd_report)
    dp = sub.add_parser("dryrun", help="生成空预测模板").set_defaults(func=cmd_dryrun)
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        # 顶层 --help
        build_parser().print_help()
        return 0
    if argv[0] in ("--version", "-V"):
        # 顶层 --version
        print("memory_eval {0}".format(__version__))
        return 0

    command = argv[0]
    sub_argv = argv[1:]
    try:
        if command == "convert":
            return cmd_convert(sub_argv)
        if command == "score":
            return cmd_score(sub_argv)
        if command == "report":
            return cmd_report(sub_argv)
        if command == "dryrun":
            return cmd_dryrun(sub_argv)
        log_warn("未知命令: {0}(可用: convert|score|report|dryrun)".format(command))
        build_parser().print_help(sys.stderr)
        return 2
    except SystemExit:
        raise
    except (ValueError, TypeError) as exc:
        log_warn("参数/数据错误: {0}".format(exc))
        return 1
    except FileNotFoundError as exc:
        log_warn("文件不存在: {0}".format(exc))
        return 1
    except Exception as exc:  # 兜底,避免 raw traceback 干扰评测
        log_warn("发生未预期错误: {0!r}".format(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
