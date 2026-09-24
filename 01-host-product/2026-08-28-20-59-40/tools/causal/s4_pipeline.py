#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""M1-08 预处理/调度总控(因果层 PoC)。

本脚本是「M1-08 全量判定 · 候选预处理 / 批调度」的总控,只做确定性预处理,
不调用任何真实 LLM。它承担四个职责:

  candidates  从 all-real-events.jsonl 生成三通道候选对,合并去重排序后输出
              candidates-m1.jsonl(默认规模 300~800)。
              三通道:
                ① 邻接  ±7d 窗口(同 corpus、from.ts ≤ to.ts)
                ② 共指  共享主体/对象(规范化后字符重叠)优先
                ③ 语义  after 关键词 2-gram 重叠(Jaccard ≥ 0.20)
                ④ 语义嵌入  Ollama bge-m3 批量嵌入 → 余弦相似(≥ --emb-threshold)
                        嵌入不可用时自动降级为③,并在 stderr 打 WARN
  batches     把候选对切成 60 对/批,输出分批清单(manifest)与「M1-07 提示词
              组装所需 json」(每批的 {pair_no, from_event, to_event} 摘要)。
  merge       读取批结果目录 s4_batches/,从含日志的回包文件中用 extract_array
              (括号配对)定位 JSON 数组,合并、按 (from,to) 去重、edge_id 全局
              重排 A001 起,输出 all-real-edges-m1.jsonl。
  report      对 edges 做统计(总数/分 type/分 strength/weak 占比/pending 数),
              并按每批 12 对随机生成编造抽检清单 s4_audit_pairs.jsonl。

约定(与提示词 v1 对齐):
  - 边类型:cause / supersede / evolve / resolve / trigger;
  - confidence > 0.6 才作为有效边,≤ 0.6 计入 pending(不入多跳);
  - 边字段:edge_id / pair_no / from_event / to_event / type / rationale /
    evidence / confidence / source_hash。

用法示例:
  python s4_pipeline.py candidates --events all-real-events.jsonl
  python s4_pipeline.py batches  --from 0 --to 600 --prompt prompt_causal_edges.md
  python s4_pipeline.py merge    --dump s4_batches
  python s4_pipeline.py report   --edges all-real-edges-m1.jsonl
"""

import argparse
import glob
import hashlib
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# 常量口径
# ---------------------------------------------------------------------------
DEFAULT_MAX_CANDIDATES = 600   # 默认候选规模上限(落在 300~800 区间)
DEFAULT_MIN_CANDIDATES = 300   # 期望下限(若数据不足则据实输出并告警)
HARD_MIN_CANDIDATES = 300      # 约定默认下限
HARD_MAX_CANDIDATES = 800      # 约定默认上限
BATCH_SIZE = 60                # 每批候选对数
WINDOW_DAYS = 7                # 邻接窗口 ±7d
STRONG_CONF = 0.8              # strength=strong 的置信度下限
PENDING_CONF = 0.6             # ≤ 该值视为 pending(不参与多跳)
SEMANTIC_MIN_OVERLAP = 0.20    # 语义通道:after 关键词 Jaccard 阈值
AUDIT_PER_BATCH = 12           # 每批编造抽检对数
STRENGTH_OF = ("strong", "medium", "weak", "unknown")


# ---------------------------------------------------------------------------
# 共享:批量回包里的 JSON 数组定位(括号配对)
# ---------------------------------------------------------------------------
def extract_array(path):
    """从可能含警告/日志前缀的回包文件中提取第一个 JSON 数组。

    兼容提示词调用方的回包文件(如 openclaw 命令行日志 + 判定结果数组)。
    策略:跳过开头的 `[xxx]` 形式日志,只接受后接 `{` `\n` ` ` 的 `[` 作为数组
    起点,再用括号配对算法数 `[`/`]` 深度,深度归零即找到数组边界。
    返回:list;找不到合法数组时返回空列表。
    """
    raw = _read_text(path)
    start = None
    for m in re.finditer(r"\[", raw):
        seg = raw[m.start(): m.start() + 2]
        if seg in ("[{", "[\n", "[ "):
            start = m.start()
            break
    if start is None:
        return []
    depth = 0
    for idx in range(start, len(raw)):
        ch = raw[idx]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start: idx + 1])
                except json.JSONDecodeError:
                    return []
    return []


def _read_text(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return fh.read()


def load_jsonl(path):
    """逐行读 JSONL,空行/解析失败的行跳过并告警。"""
    items = []
    with open(path, "r", encoding="utf-8-sig") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print("WARN: %s 第 %d 行 JSON 解析失败,跳过: %s"
                      % (path, lineno, exc), file=sys.stderr)
    return items


# ---------------------------------------------------------------------------
# 文本规范化/关键词
# ---------------------------------------------------------------------------
def norm(s):
    """去除标点/空白,返回紧凑字符串,用于主体/对象/关键词比较。"""
    return re.sub(r"[\s（）()【】\[\],。·、:：\-—/\\]", "", s or "")


def ngrams(s, n=2):
    """取 n-gram 集合;长度不足 n 时返回 {s}。"""
    s = norm(s)
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def jaccard(a, b):
    """Jaccard 相似度;空集返回 0.0。"""
    A, B = set(a or []), set(b or [])
    if not A or not B:
        return 0.0
    return len(A & B) / max(1, len(A | B))


def day(ts):
    """把 yyyy-mm-dd 解析成 date;失败返回 None。"""
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def sha12(text):
    """对文本取 sha256 前 12 位,作为 source_hash 占位(可与提示词一致)。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# 语义通道的 embedding 接口(实现版:Ollama bge-m3;失败回退 None → 关键词重叠兜底)
# ---------------------------------------------------------------------------
EMBED_API = "http://localhost:11434"
EMBED_MODEL = "bge-m3"
# 单请求条数上限。实测(2026-09-20):一次投 393 条时 Ollama 必返 HTTP 400
# (`Post http://127.0.0.1:<port>/tokenize: connectex: actively refused`,
#  即内部 tokenize 子服务在大批量下不可靠);<=256 条稳定,按 32 条分批 13/13 段全过。
# 取 64 留足余量。
EMBED_BATCH = 64
_EMB_CACHE = {}


def embed_fn(text):
    """语义通道接入真实 embedding:调用 Ollama /api/embeddings(bge-m3),带缓存;失败返回 None。"""
    try:
        import hashlib as _h
        import json as _j
        import urllib.request as _ur
        key = _h.sha256(text.encode("utf-8")).hexdigest()
        if key in _EMB_CACHE:
            return _EMB_CACHE[key]
        req = _ur.Request(EMBED_API + "/api/embeddings",
                          data=_j.dumps({"model": EMBED_MODEL, "prompt": text[:2000]}).encode(),
                          headers={"Content-Type": "application/json"})
        v = _j.load(_ur.urlopen(req, timeout=20)).get("embedding", [])
        _EMB_CACHE[key] = v
        return v
    except Exception:
        return None


def _after_keywords(ev):
    """从事件 after(回填 before 与 object 补强)抽取关键词 bigram 集合。"""
    parts = [ev.get("after") or "", ev.get("before") or "", ev.get("object") or ""]
    return ngrams(" ".join(p for p in parts if p), 2)


# ---------------------------------------------------------------------------
# 三通道候选生成
# ---------------------------------------------------------------------------
def _ordered_pairs(events):
    """产出有序 (from,to) 对的范围约定:from.ts ≤ to.ts(均已知时才强制)。"""
    return events


def _temporal_ok(a, b):
    ad, bd = day(a.get("ts")), day(b.get("ts"))
    if ad and bd and ad > bd:
        return False
    return True


def channel_adjacency(events, window_days=WINDOW_DAYS):
    """通道①邻接:同 corpus、时间差 ∈ [0, window_days] 的有序对。"""
    by_corpus = defaultdict(list)
    for e in events:
        by_corpus[e.get("corpus")].append(e)

    out = []  # (from_key, to_key, days_diff)
    for corpus, evs in by_corpus.items():
        # 未知时间的事件不参与邻接(无法排序),放到末尾避免误配。
        known = [(e, day(e.get("ts"))) for e in evs]
        known = [(e, d) for e, d in known if d is not None]
        known.sort(key=lambda x: x[1])  # 按时间升序
        for i in range(len(known)):
            ae, ad = known[i]
            for j in range(i + 1, len(known)):
                be, bd = known[j]
                diff = (bd - ad).days
                if diff > window_days:
                    break  # 已按时间排序,后面只会更远
                if diff < 0:
                    continue
                out.append((ae["event_id"], be["event_id"], diff))
    return out


def _subject_tokens(ev):
    return norm(ev.get("subject")) + norm(ev.get("object"))


def channel_coref(events):
    """通道②共指:共享主体/对象字符片段的有序对(同 corpus)。"""
    groups = defaultdict(list)  # 规范主体 -> [(event,id)]
    for e in events:
        subj = norm(e.get("subject"))
        if subj:
            groups[subj].append(e)
    out = []
    for subj, evs in groups.items():
        for i in range(len(evs)):
            for j in range(len(evs)):
                if i == j:
                    continue
                a, b = evs[i], evs[j]
                if a.get("corpus") != b.get("corpus"):
                    continue
                if not _temporal_ok(a, b):
                    continue
                out.append((a["event_id"], b["event_id"], "coref"))
    return out


def channel_semantic(events, overlap_threshold=SEMANTIC_MIN_OVERLAP):
    """通道③语义:after 关键词 2-gram 重叠(Jaccard ≥ SEMANTIC_MIN_OVERLAP)。

    这是**始终可用**的兜底通道;真嵌入版见 channel_semantic_emb。
    """
    # 建立 bigram -> 事件倒排,仅比较至少共享一个关键词的对,避免全量两两。
    bucket = defaultdict(list)
    for e in events:
        for g in _after_keywords(e):
            bucket[g].append(e["event_id"])
    seen = set()
    out = []
    for g, eids in bucket.items():
        for i in range(len(eids)):
            for j in range(i + 1, len(eids)):
                key = (eids[i], eids[j])
                if key in seen:
                    continue
                seen.add(key)
                a = _event_by_id(events, eids[i])
                b = _event_by_id(events, eids[j])
                if a is None or b is None:
                    continue
                if a.get("corpus") != b.get("corpus"):
                    continue
                if not _temporal_ok(a, b):
                    continue
                ov = jaccard(_after_keywords(a), _after_keywords(b))
                if ov >= overlap_threshold:
                    out.append((a["event_id"], b["event_id"], ov))
    return out


def embed_batch(texts, batch_size=EMBED_BATCH, retries=3):
    """批量嵌入(Ollama /api/embed,按 batch_size 分批 + 重试);整体失败返回 None。

    ⚠️ 失败**必须可见**:早先版本对单次请求 `except Exception: return None` 静默吞错,
    使 semantic-emb 通道在 393 条规模下约 60% 概率静默消失
    (候选池 11,865 → 11,846),冻结产物因此不可稳定复现。
    现在失败会打印 WARN,调用方据此降级为关键词通道。
    """
    import json as _j
    import urllib.request as _ur
    texts = texts[:1000]
    out = []
    for off in range(0, len(texts), batch_size):
        seg = texts[off:off + batch_size]
        vecs = None
        for attempt in range(1, retries + 1):
            try:
                req = _ur.Request(EMBED_API + "/api/embed",
                                  data=_j.dumps({"model": EMBED_MODEL, "input": seg}).encode(),
                                  headers={"Content-Type": "application/json"})
                resp = _j.load(_ur.urlopen(req, timeout=120))
                got = resp.get("embeddings") or None
                if got and len(got) == len(seg):
                    vecs = got
                    break
                print("WARN: embedding 批次 [%d,%d) 回包条数异常(第 %d/%d 次)"
                      % (off, off + len(seg), attempt, retries), file=sys.stderr)
            except Exception as exc:
                if attempt == retries:
                    print("WARN: embedding 批次 [%d,%d) 连续失败 %d 次:%s"
                          % (off, off + len(seg), retries, exc), file=sys.stderr)
        if vecs is None:
            print("WARN: semantic-emb 通道降级 —— 批次 [%d,%d) 不可用,"
                  "本次候选退回关键词通道" % (off, off + len(seg)), file=sys.stderr)
            return None
        out.extend(vecs)
    return out or None


def _event_by_id(events, eid):
    for e in events:
        if e.get("event_id") == eid:
            return e
    return None


def channel_semantic_emb(events, threshold=0.6, top_per_change=12):
    """通道④语义嵌入:批量 bge-m3 嵌入 → 余弦相似(同 corpus、时间序,≥阈值)。

    以 change/bugfix 为中心取 top_k;嵌入不可用时返回空(关键词兜底见 channel_semantic),
    并在 stderr 打 WARN。
    """
    texts = {}
    for e in events:
        texts[e["event_id"]] = ((e.get("subject") or "") + (e.get("object") or "")
                                + (e.get("after") or ""))[:500]
    vecs = embed_batch([texts[e["event_id"]] for e in events])
    if not vecs or len(vecs) != len(events):
        # 本分支 = embedding 整体不可用，而**不是**「无命中」。
        # 具体批次失败原因已由 embed_batch 打到 stderr，这里给通道级结论。
        print("WARN: semantic-emb 通道未生效(embedding 不可用)，"
              "本次候选仅由 邻接/共指/关键词 产出", file=sys.stderr)
        return []
    vectors = {e["event_id"]: vecs[i] for i, e in enumerate(events)}

    def cos(a, b):
        if not a or not b or len(a) != len(b):
            return 0.0
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        if not na or not nb:
            return 0.0
        return sum(x * y for x, y in zip(a, b)) / (na * nb)

    out = []
    for c in events:
        if c.get("type") not in ("change", "bugfix"):
            continue
        cv = vectors.get(c["event_id"])
        if cv is None:
            continue
        cands = []
        for o in events:
            if o["event_id"] == c["event_id"] or o.get("corpus") != c.get("corpus"):
                continue
            if not _temporal_ok(c, o):
                continue
            s = cos(cv, vectors.get(o["event_id"]))
            if s >= threshold:
                cands.append((round(s, 3), o["event_id"]))
        cands.sort(reverse=True)
        for s, oid in cands[:top_per_change]:
            out.append((c["event_id"], oid, s))
    return out


def _shared_score(a, b):
    """共指强度:同主体(2.0)> 主体/对象共享 ≥2 字片段(1.0)> 单字重叠(0.2)。"""
    sa, sb = norm(a.get("subject")), norm(b.get("subject"))
    ta, tb = _subject_tokens(a), _subject_tokens(b)
    # 同主体(规范化相等):属强共指
    if sa and sb and sa == sb:
        return 2.0
    # 主体或对象共享 ≥2 字连续片段
    if any(x and y and min(len(x), len(y)) >= 2 and (x in y or y in x)
           for x, y in [(ta, tb)]):
        return 1.0
    # 任意单字重叠:弱共指
    if set(ta) & set(tb):
        return 0.2
    return 0.0


# ---------------------------------------------------------------------------
# candidates 子命令
# ---------------------------------------------------------------------------
def cmd_candidates(args):
    events = load_jsonl(args.events)
    by_id = {e["event_id"]: e for e in events}
    print("读取事件数:", len(events))

    # 三通道,结果合并到 unified[(from,to)]。
    unified = {}

    for (fm, to, diff) in channel_adjacency(events, args.window_days):
        rec = unified.setdefault((fm, to), {"channels": [], "days_diff": None})
        rec["channels"].append("adjacency")
        if rec["days_diff"] is None or diff < rec["days_diff"]:
            rec["days_diff"] = diff

    for (fm, to, _tag) in channel_coref(events):
        rec = unified.setdefault((fm, to), {"channels": [], "days_diff": None})
        if "coref" not in rec["channels"]:
            rec["channels"].append("coref")

    for (fm, to, ov) in channel_semantic(events):
        rec = unified.setdefault((fm, to), {"channels": [], "days_diff": None})
        if "semantic" not in rec["channels"]:
            rec["channels"].append("semantic")
        rec["semantic_overlap"] = max(
            rec.get("semantic_overlap", 0.0), ov)

    for (fm, to, sim) in channel_semantic_emb(events, threshold=args.emb_threshold,
                                              top_per_change=args.emb_top_per_change):
        rec = unified.setdefault((fm, to), {"channels": [], "days_diff": None})
        if "semantic-emb" not in rec["channels"]:
            rec["channels"].append("semantic-emb")
        rec["semantic_emb"] = max(rec.get("semantic_emb", 0.0), sim)

    candidates = []
    for (fm, to), meta in unified.items():
        a, b = by_id.get(fm), by_id.get(to)
        if a is None or b is None:
            continue
        score = 0.0
        # 时序近邻奖赏:越近越高
        if meta["days_diff"] is not None:
            score += 1.0 * (1 - min(meta["days_diff"], args.window_days) / args.window_days)
        # 共指奖赏
        score += _shared_score(a, b)
        # 语义奖赏(0~2)
        score += 2.0 * meta.get("semantic_overlap", 0.0)
        # 嵌入语义奖赏(0~2)
        score += 2.0 * max(meta.get("semantic_emb", 0.0), meta.get("semantic_overlap", 0.0))
        candidates.append({
            "from_event": fm,
            "to_event": to,
            "from_ts": a.get("ts"),
            "to_ts": b.get("ts"),
            "from_subj": a.get("subject"),
            "to_subj": b.get("subject"),
            "channels": meta["channels"],
            "days_diff": meta["days_diff"],
            "score": round(score, 4),
        })

    # 排序:得分降序,再按 (from,to) 确定性排序。
    candidates.sort(key=lambda c: (-c["score"], c["from_event"], c["to_event"]))

    # 规模:默认落在 [300,800]。超过 max 则截断;低于 min 则据实输出并告警。
    max_n = args.max_candidates if args.max_candidates else DEFAULT_MAX_CANDIDATES
    picked = candidates[:max_n]
    if len(candidates) < DEFAULT_MIN_CANDIDATES:
        print("WARN: 候选池仅 %d 对,低于期望下限 %d(数据使然)"
              % (len(candidates), DEFAULT_MIN_CANDIDATES), file=sys.stderr)

    with open(args.out, "w", encoding="utf-8") as fh:
        for c in picked:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")

    print("候选输出: %d / 全量候选 %d → %s" % (len(picked), len(candidates), args.out))
    # 通道分布统计
    ch_cnt = Counter()
    for c in picked:
        for ch in c["channels"]:
            ch_cnt[ch] += 1
    print("各通道覆盖(候选去重后,可多通道):", dict(ch_cnt))
    return 0


# ---------------------------------------------------------------------------
# batches 子命令
# ---------------------------------------------------------------------------
def summarize(e):
    """生成提示词输入所需的事件摘要(与 compose_pairs_payload 口径一致)。"""
    return ("%s|%s|%s|%s %s %s → %s" % (
        e.get("event_id"), e.get("ts"), e.get("type"),
        e.get("subject"), e.get("action") or "", e.get("object") or "",
        e.get("after") or ""))


def cmd_batches(args):
    cands = load_jsonl(args.candidates)
    evs = {}
    if args.events:
        for e in load_jsonl(args.events):
            evs[e["event_id"]] = e
    else:
        # 若未给 events,尝试从候选旁的实现里推断;此处仅提示。
        print("WARN: 未给 --events,事件摘要留空(from/to 仅含原始 id)", file=sys.stderr)

    start = args.from_ or 0
    end = args.to if args.to is not None else len(cands)
    end = min(end, len(cands))
    if start >= end:
        print("WARN: 区间 [%d, %d) 为空或越界(候选共 %d 对)" % (start, end, len(cands)))
        return 0

    sliced = cands[start:end]
    os.makedirs(args.dump, exist_ok=True)

    manifest = []
    rows = []
    for k in range(0, len(sliced), BATCH_SIZE):
        chun = sliced[k:k + BATCH_SIZE]
        batch_no = len(manifest) + 1
        g_from = start + k
        g_to = g_from + len(chun)
        pairs = []
        for i, c in enumerate(chun, 1):
            a, b = evs.get(c["from_event"]), evs.get(c["to_event"])
            pairs.append({
                "pair_no": i,
                "from_event": summarize(a) if a else c["from_event"],
                "to_event": summarize(b) if b else c["to_event"],
                "from_id": c["from_event"],
                "to_id": c["to_event"],
            })
        # 与本批输入内容一致的 hash,作为模型 source_hash 占位。
        text_hash = sha12(json.dumps(pairs, ensure_ascii=False))
        payload = {
            "batch_no": batch_no,
            "from": g_from,
            "to": g_to,
            "count": len(chun),
            "text_hash": text_hash,
            "pairs": pairs,
        }
        payload_path = os.path.join(args.dump, "batch_%03d.payload.json" % batch_no)
        result_path = os.path.join(args.dump, "batch_%03d.result.txt" % batch_no)
        with open(payload_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)

        manifest.append({
            "batch_no": batch_no,
            "from": g_from,
            "to": g_to,
            "count": len(chun),
            "text_hash": text_hash,
            "payload_path": payload_path,
            "result_path": result_path,
        })
        rows.append((batch_no, g_from, g_to, len(chun), text_hash))

    # 分批清单:同时落盘 manifest 并在终端打印表格。
    mpath = args.manifest
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)

    print("分批清单(共 %d 批,每批 ≤%d 对):" % (len(manifest), BATCH_SIZE))
    print("  batch | pair_from | pair_to | count | text_hash")
    for (no, f, t, cnt, th) in rows:
        print("  %5d | %9d | %9d | %5d | %s" % (no, f, t - 1, cnt, th))
    print("清单:", mpath)
    print("每批组装 json → %s/batch_NNN.payload.json" % args.dump)
    print("模型回包请保存为 → %s/batch_NNN.result.txt(merge 将从此目录提取)" % args.dump)
    return 0


# ---------------------------------------------------------------------------
# merge 子命令
# ---------------------------------------------------------------------------
def _edge_of(obj):
    """判断一条记录是否为判边(含 from_event 与 to_event)。"""
    return isinstance(obj, dict) and obj.get("from_event") and obj.get("to_event")


def _load_batch_result(path):
    """从一个批结果文件提取边列表。

    优先按 extract_array(括号配对)定位;若文件本身是纯 JSON 数组/JSONL,
    直接解析。返回 list(可能为空)。
    """
    raw = _read_text(path).lstrip()
    if raw.startswith("["):
        arr = extract_array(path)
        if isinstance(arr, list):
            return arr
    # 尝试整行 JSONL(每个元素一行)
    try:
        items = load_jsonl(path)
        if items and all(_edge_of(x) for x in items):
            return items
    except (OSError, ValueError):
        pass
    arr = extract_array(path)
    return arr if isinstance(arr, list) else []


def cmd_merge(args):
    pattern = args.pattern or "*.result.txt"
    files = []
    if args.dump and os.path.isdir(args.dump):
        # 在批结果目录内,对每个逗号分隔的 glob 模式展开。
        for pat in pattern.split(","):
            pat = pat.strip()
            if not pat:
                continue
            if os.path.isabs(pat):
                files += glob.glob(pat)
            else:
                files += glob.glob(os.path.join(args.dump, pat))
        # 若按默认模式没命中,兼容「批结果直接以 *_result.txt 保存」的旧命名。
        if not files:
            files += [os.path.join(args.dump, n)
                      for n in os.listdir(args.dump)
                      if n.endswith("_result.txt") or n.endswith(".result.txt")]
    else:
        for pat in pattern.split(","):
            pat = pat.strip()
            if pat:
                files += glob.glob(pat)
    files = sorted(set(files))

    all_edges = []
    per_file = []
    for path in files:
        arr = _load_batch_result(path)
        edges = [e for e in arr if _edge_of(e)]
        if edges:
            # 记录来源文件,便于回溯(不写入输出边本身)。
            for e in edges:
                e = dict(e)
                e.setdefault("_src", path)
                all_edges.append(e)
        per_file.append((path, len(edges)))

    # 按 (from,to) 去重,保留最先出现(首个批)的边。
    seen = {}
    dup = 0
    for e in all_edges:
        key = (e.get("from_event"), e.get("to_event"))
        if key in seen:
            dup += 1
            continue
        seen[key] = e
    deduped = list(seen.values())

    # edge_id 全局顺序重排 A001 起;顺序按 (from,to) 确定性排序。
    deduped.sort(key=lambda e: (e.get("from_event"), e.get("to_event")))
    for i, e in enumerate(deduped, 1):
        e["edge_id"] = "A%03d" % i
        e.pop("_src", None)

    with open(args.out, "w", encoding="utf-8") as fh:
        for e in deduped:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")

    print("合并批结果:")
    for (path, n) in per_file:
        print("  %-50s 边=%d" % (path, n))
    print("原始边:%d;去重(by from,to)后:%d;重复丢弃:%d"
          % (len(all_edges), len(deduped), dup))
    print("输出:%s → 共 %d 条边,edge_id 自 A001 起" % (args.out, len(deduped)))
    return 0


# ---------------------------------------------------------------------------
# report 子命令
# ---------------------------------------------------------------------------
def strength_of(conf):
    """按置信度映射 strength(报告用,边本身不新增字段)。"""
    if conf is None:
        return "unknown"
    if conf > STRONG_CONF:
        return "strong"
    if conf > PENDING_CONF:
        return "medium"
    return "weak"


def cmd_report(args):
    edges = load_jsonl(args.edges) if os.path.exists(args.edges) else []
    print("edges 总数: %d" % len(edges))
    by_type = Counter(e.get("type") for e in edges)
    print("分 type:", dict(by_type))

    by_strength = Counter(strength_of(e.get("confidence")) for e in edges)
    print("分 strength:", dict(by_strength))

    pending = [e for e in edges if (e.get("confidence") or 0) <= PENDING_CONF]
    weak = by_strength["weak"] + by_strength["unknown"]
    weak_ratio = (weak / len(edges)) if edges else 0.0
    print("weak 占比: %.1f%%(%d / %d)" % (weak_ratio * 100, weak, len(edges)))
    print("pending 数(confidence ≤ %.2f): %d" % (PENDING_CONF, len(pending)))

    # ---- 编造抽检清单:每批 12 对随机 ----
    audit = _build_audit(args)
    n_audit = 0
    with open(args.audit_out, "w", encoding="utf-8") as fh:
        for rec in audit:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n_audit += 1
    print("编造抽检清单: %d 对 → %s" % (n_audit, args.audit_out))
    return 0


def _build_audit(args):
    """生成编造抽检清单。

    逻辑:把边按批归属(manifest 记录候选区间,或用候选序号倒推),每批随机抽
    AUDIT_PER_BATCH 对,连同 from/to 原始摘要与判边字段一起落盘,供人工判定
    「语料是否有依据 → 过提取 / 编造」。
    """
    cands = []
    if args.candidates and os.path.exists(args.candidates):
        cands = load_jsonl(args.candidates)
    idx_of = {}
    for i, c in enumerate(cands):
        idx_of[(c.get("from_event"), c.get("to_event"))] = i

    manifest = []
    if args.manifest and os.path.exists(args.manifest):
        with open(args.manifest, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    if not manifest:
        # 无 manifest 则按候选序号切 60 对/批估一个批边界。
        n = len(cands)
        manifest = []
        for k in range(0, n, BATCH_SIZE):
            manifest.append({"batch_no": len(manifest) + 1,
                             "from": k, "to": min(k + BATCH_SIZE, n)})

    events = {}
    if args.events and os.path.exists(args.events):
        for e in load_jsonl(args.events):
            events[e["event_id"]] = e

    edges = load_jsonl(args.edges) if os.path.exists(args.edges) else []
    # 把边所在候选序号还原出来,用于分桶。
    edge_bucket = {}  # batch_no -> [edge]
    for e in edges:
        gi = idx_of.get((e.get("from_event"), e.get("to_event")))
        bno = None
        for m in manifest:
            if gi is not None and m["from"] <= gi < m["to"]:
                bno = m["batch_no"]
                break
        if bno is None:
            bno = 0  # 未映射到具体的批,归到编号 0(单独一桶)
        edge_bucket.setdefault(bno, []).append(e)

    rng = random.Random(args.seed)
    audit = []
    for bno in sorted(edge_bucket):
        pool = edge_bucket[bno]
        sample = rng.sample(pool, min(AUDIT_PER_BATCH, len(pool)))
        for e in sample:
            rec = {
                "batch_no": bno,
                "edge_id": e.get("edge_id"),
                "from_event": e.get("from_event"),
                "to_event": e.get("to_event"),
                "type": e.get("type"),
                "confidence": e.get("confidence"),
                "rationale": e.get("rationale"),
                "evidence": e.get("evidence"),
                "from_original": _original_of(events.get(e.get("from_event"))),
                "to_original": _original_of(events.get(e.get("to_event"))),
            }
            audit.append(rec)
    return audit


def _original_of(ev):
    if not ev:
        return None
    return "%s|%s|%s|%s %s %s → %s" % (
        ev.get("ts"), ev.get("type"), ev.get("subject"),
        ev.get("action") or "", ev.get("object") or "", ev.get("before") or "",
        ev.get("after") or "")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="M1-08 预处理/调度总控(不调真实 LLM)")
    sub = ap.add_subparsers(dest="command", required=True)

    p_c = sub.add_parser("candidates", help="生成三通道候选 → candidates-m1.jsonl")
    p_c.add_argument("--events", default="all-real-events.jsonl")
    p_c.add_argument("--out", default="candidates-m1.jsonl")
    p_c.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES)
    p_c.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    p_c.add_argument("--emb-threshold", type=float, default=0.6)
    p_c.add_argument("--emb-top-per-change", type=int, default=12)
    p_c.set_defaults(func=cmd_candidates)

    p_b = sub.add_parser("batches", help="把候选切成 60 对/批,输出清单与组装 json")
    p_b.add_argument("--candidates", default="candidates-m1.jsonl")
    p_b.add_argument("--events", default="all-real-events.jsonl",
                     help="事件详情(用于生成 from/to 摘要)")
    p_b.add_argument("--from", dest="from_", type=int, default=0,
                     help="候选全局起点索引(含)")
    p_b.add_argument("--to", dest="to", type=int, default=None,
                     help="候选全局终点索引(不含);默认到末尾")
    p_b.add_argument("--dump", default="s4_batches", help="批输出目录")
    p_b.add_argument("--manifest", default="s4_batches_manifest.json")
    p_b.set_defaults(func=cmd_batches)

    p_m = sub.add_parser("merge", help="合并批结果,去重重排 → all-real-edges-m1.jsonl")
    p_m.add_argument("--dump", default="s4_batches", help="批结果目录")
    p_m.add_argument("--pattern", default="*.result.txt",
                     help="结果文件 glob 默认")
    p_m.add_argument("--out", default="all-real-edges-m1.jsonl")
    p_m.set_defaults(func=cmd_merge)

    p_r = sub.add_parser("report", help="边统计 + 编造抽检清单")
    p_r.add_argument("--edges", default="all-real-edges-m1.jsonl")
    p_r.add_argument("--candidates", default="candidates-m1.jsonl")
    p_r.add_argument("--manifest", default="s4_batches_manifest.json")
    p_r.add_argument("--events", default="all-real-events.jsonl")
    p_r.add_argument("--audit-out", default="s4_audit_pairs.jsonl")
    p_r.add_argument("--seed", type=int, default=20260829,
                     help="抽检随机种子(可复现)")
    p_r.set_defaults(func=cmd_report)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
