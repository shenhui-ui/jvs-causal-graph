#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""llm_answer.py — M1-10 LLM 生成层：用 sensenova(deepseek-v4-pro) 把证据链织成自然语言答案。

输入: causal_graph_final.db + query-set-real-v1.jsonl（复用 m1_10_pipeline 的检索/链路逻辑）
输出: <out>/answers-llm.jsonl（与管线 answers.jsonl 同 schema，answer_text 为 LLM 生成）
限速: 串行调用,题间 sleep 16s;HTTP 429 时 sleep 66s 重试(最多 6 次);逐题落盘防丢。
用法:
  python llm_answer.py --db causal_graph_final.db \
      --questions query-set-real-v1.jsonl --out m1_10_baseline_llm
"""
import argparse
import io
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import causal_graph as cg
from m1_10_pipeline import load_events, extract_keywords, retrieve, _search_text


def _edge_expandable(edge):
    """只有非 weak/pending 且置信度高于 0.6 的边才可继续多跳。"""
    if edge.get("strength") in ("weak", "pending"):
        return False
    confidence = edge.get("confidence")
    if confidence is None:
        return False
    try:
        return float(confidence) > 0.6
    except (TypeError, ValueError):
        return False

API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
MODEL = "doubao"
CRED = r"C:\Users\<user>\.dsh\.credentials.yaml"
GAP_SECONDS = int(os.environ.get("LLM_GAP", "16"))
RETRY_WAIT = 66
MAX_RETRY = 6

# provider 配置: doubao(默认,火山方舟) | sensenova(备选)
PROVIDERS = {
    "doubao": {
        "url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "key_env": "DOUBAO_API_KEY", "key_field": "DOUBAO_API_KEY",
        "model_field": "DOUBAO_MODEL", "default_model": "doubao-seed-1-6-250615",
    },
    "sensenova": {
        "url": "https://token.sensenova.cn/v1/chat/completions",
        "key_env": "SENSENOVA1_API_KEY", "key_field": "SENSENOVA1_API_KEY",
        "model_field": None, "default_model": "deepseek-v4-pro",
    },
    "doubao_sub": {  # 豆包订阅本地桥(doubao2api QR 登录),OpenAI 兼容; DOUBAO_SUB_URL 可指 9091(新会话模式)
        "url": os.environ.get("DOUBAO_SUB_URL", "http://127.0.0.1:9090/v1/chat/completions"),
        "key_env": "DOUBAO_SUB_KEY", "key_field": "DOUBAO_SUB_KEY",
        "model_field": None, "default_model": "doubao",
    },
}


def provider_conf(provider=None):
    p = (provider or os.environ.get("LLM_PROVIDER", "") or "doubao").lower()
    c = PROVIDERS.get(p) or PROVIDERS["doubao"]
    key = os.environ.get(c["key_env"]) or ""
    if not key:
        with io.open(CRED, encoding="utf-8") as f:
            for line in f:
                m = re.search(rf"{c['key_field']}:\s*(\S+)", line)
                if m:
                    key = m.group(1)
                    break
    if not key:
        raise RuntimeError(f"{c['key_field']} not found in env or {CRED}")
    model = os.environ.get("DOUBAO_MODEL") if c is PROVIDERS["doubao"] else None
    if not model and c.get("model_field"):
        with io.open(CRED, encoding="utf-8") as f:
            for line in f:
                m = re.search(rf"{c['model_field']}:\s*(\S+)", line)
                if m:
                    model = m.group(1)
                    break
    # 2026-09-04: 通用模型覆盖(对比实验用;不设则保持原默认)
    model = os.environ.get("LLM_MODEL") or model or c["default_model"]
    return {"provider": p, "url": c["url"], "key": key,
            "model": model}


def chat(messages, max_tokens=None, temperature=0.3, provider=None):
    if max_tokens is None:  # 生成层默认 700;思考型模型(sensenova)需 LLM_MAX_TOKENS=4096 预算覆盖 reasoning
        max_tokens = int(os.environ.get("LLM_MAX_TOKENS", "700"))
    conf = provider_conf(provider)
    body = json.dumps({"model": conf["model"], "messages": messages,
                       "temperature": temperature, "max_tokens": max_tokens}).encode("utf-8")
    req = urllib.request.Request(conf["url"], data=body, method="POST", headers={
        "Authorization": "Bearer " + conf["key"], "Content-Type": "application/json"})
    last = ""
    for attempt in range(MAX_RETRY + 1):
        try:
            with urllib.request.urlopen(req, timeout=360) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
            if e.code == 429 and attempt < MAX_RETRY:
                print(f"[429] wait {RETRY_WAIT}s (attempt {attempt + 1})", flush=True)
                time.sleep(RETRY_WAIT)
                continue
            raise RuntimeError(last)
        except Exception as e:  # noqa: BLE001
            last = repr(e)
            if attempt < MAX_RETRY:
                time.sleep(10)
                continue
            raise RuntimeError(last)
    raise RuntimeError(last)


def chain_recall(db_path, event_id, max_hops=3):
    """证据链召回：低置信边可展示为一跳线索，但不可驱动下一跳扩展。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, from_event, to_event, type, rationale, confidence,"
            " source_hash, evidence, strength FROM causal_edges").fetchall()
    finally:
        conn.close()
    fwd, bwd = {}, {}
    for r in rows:
        e = dict(r)
        # 保留低置信边作为当前节点的一跳线索；是否可继续扩展由 BFS 单独判断。
        fwd.setdefault(e["from_event"], []).append((e, e["to_event"]))
        bwd.setdefault(e["to_event"], []).append((e, e["from_event"]))
    visited = {event_id}
    used, skipped_rev = [], 0
    # 工程修复 v8-rev: LLM_REV_EDGES=1 时反向(cause/trigger)边纳入证据链。
    # 旧行为反向边只计数不进链——问"为什么X"时锚点是结果事件,上游原因永远召不回,
    # 这正是 cause_hit 长期瓶颈的根因(v3 补边方向全是 原因→结果, 入边不受此修复影响)。
    rev_ok = bool(os.environ.get("LLM_REV_EDGES"))
    queue = deque([(event_id, 0)])
    while queue:
        node, depth = queue.popleft()
        if depth >= max_hops:
            continue
        cand = [(e, n, False) for e, n in fwd.get(node, []) if n not in visited]
        cand += [(e, p, True) for e, p in bwd.get(node, [])
                 if p not in visited and e.get("type") in ("cause", "trigger")]
        for e, nxt, is_rev in sorted(cand, key=lambda x: 0 if not x[2] else 1):
            if nxt in visited:
                continue
            if is_rev and not rev_ok:
                skipped_rev += 1
                continue
            visited.add(nxt)
            used.append({**e, "expandable": _edge_expandable(e)})
            if _edge_expandable(e):
                queue.append((nxt, depth + 1))
    return {"ok": True, "edges": used, "skipped_reverse": skipped_rev}


def _drop_digit_grams(seq):
    """剔除**纯数字**片段（关键词与 2-gram 通用）。

    题面里的日期 `2026-08-12` 会被切成 `-0` `02` `08` `20` `26` `6-` `8-`
    这类碎片，它们能匹配**任意事件日期串**（_search_text 含 ts），
    使无关事件靠日期碎片刷高/刷平命中数，真值反被压后。
    只剔纯数字：题面数字与事件 ts 的重合在既有题集上是有效信号，
    整段剔除会伤回归（实测位次0 由 27/30 掉到 25/30）。
    """
    return [x for x in seq if x and not str(x).isdigit()]


def _question_date(question):
    """从题面自带日期提取基准日（ISO 或中文日/月）。读不到则返回 None。

    只用题面文本，不读题集的 group/chunk 等元数据
    （避免把答案位置泄露给检索）。
    """
    q = question or ""
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", q)
    if m:
        return "%s-%s-%s" % m.group(1, 2, 3)
    m = re.search(r"(20\d{2})-(\d{2})", q)
    if m:
        return "%s-%s-15" % m.group(1, 2)
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", q)
    if m:
        return "2026-%02d-%02d" % (int(m.group(1)), int(m.group(2)))
    m = re.search(r"(\d{1,2})\s*月", q)
    if m:
        return "2026-%02d-15" % int(m.group(1))
    return None


def _date_proximity(event, qdate):
    """返回负的日期距离（越大越近）；不可用时返回 None。

    作为关键词命中数相同时的**主排序键**：题面日期给出的是
    「事件应发生在何时」的强信号，比易被数字碎片污染的
    2-gram 重合度更可靠。
    """
    if not qdate:
        return None
    ts = str(event.get("ts") or "")[:10]
    if len(ts) != 10:
        return None
    try:
        from datetime import datetime
        return -abs((datetime.strptime(ts, "%Y-%m-%d")
                     - datetime.strptime(qdate, "%Y-%m-%d")).days)
    except Exception:  # noqa: BLE001
        return None


def retrieve_top(events, kws, k=3, question=None, date_prox=True):
    """多锚点检索：取命中词数 top-K。
    同分按类型偏好 + 与题面日期的距离（date_prox）排序；
    题面无日期（或 date_prox=False）时退化为原行为。
    关键词与排序片段均先剔除纯数字碎片（口径与 retrieve_hybrid 一致）。
    """
    kws = _drop_digit_grams(kws)
    scored = []
    for e in events:
        txt = _search_text(e)
        hits = sum(1 for kw in kws if kw and kw in txt)
        if hits:
            scored.append((hits, e))
    if not scored:
        return []

    qdate = _question_date(question) if date_prox else None

    def rank(x):
        n, e = x
        pref = 1 if e.get("type") in ("change", "decision", "bugfix") else 0
        prox = _date_proximity(e, qdate)
        if prox is None:  # 无可用日期：保留原行为（含 ts 降序）
            return (n, pref, 0, e.get("ts") or "")
        return (n, pref, 1, prox)

    scored.sort(key=rank, reverse=True)
    seen, out = set(), []
    for _, e in scored:
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        out.append(e)
        if len(out) >= k:
            break
    return out


def retrieve_hybrid(events, kws, question, k=3, min_bigram=6, date_prox=True):
    """混合检索（round-4）：关键词命中数 + 题面 2-gram 重合度双轨。
    关键词命中为先、二元组重合为次——解决转述式题面关键词抽取成
    长尾句污染、真实事件因缺关键词命中而漏召的问题(v2 Q104/109/110 类)。"""
    q = question or ""
    qdate = _question_date(q) if date_prox else None
    kws = _drop_digit_grams(kws)
    grams = {q[i:i + 2] for i in range(len(q) - 1)
             if q[i] != ' ' and q[i:i+2][1] != ' '}
    grams = _drop_digit_grams(grams)
    scored = []
    for e in events:
        txt = _search_text(e)
        kh = sum(1 for kw in kws if kw and kw in txt)
        bh = sum(1 for g in grams if g in txt)
        if kh > 0 or bh >= min_bigram:
            prox = _date_proximity(e, qdate)
            if prox is None:
                # 题面无日期：退化为原 (kh, bh) 行为
                key = (kh, 0, 0, bh)
            else:
                key = (kh, 1, prox, bh)
            scored.append((key, e))
    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    out, seen = [], set()
    for _, e in scored:
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        out.append(e)
        if len(out) >= k:
            break
    return out


def _event_scope(event):
    """Return explicit corpus, or a conservative source-hash scope for legacy DB rows."""
    return event.get("corpus") or event.get("source_hash")


def temporal_neighbors(evs_all, anchor, days=12, limit=5):
    """同语料 ±days 天内的时间邻近事件；旧库无 corpus 时退化为同 source_hash。"""
    if not anchor:
        return []
    try:
        from datetime import datetime
        base = datetime.strptime(anchor.get("ts", "unknown"), "%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return []
    cand = []
    for e in evs_all:
        e_id = e.get("id") or e.get("event_id")
        anchor_id = anchor.get("id") or anchor.get("event_id")
        if e_id == anchor_id:
            continue
        # 缺失 corpus 时不把两个未知来源误判为同语料；只有明确相同语料才纳入。
        anchor_scope = _event_scope(anchor)
        event_scope = _event_scope(e)
        if not anchor_scope or not event_scope or event_scope != anchor_scope:
            continue
        try:
            d = datetime.strptime(e.get("ts", ""), "%Y-%m-%d")
        except Exception:  # noqa: BLE001
            continue
        delta = (d - base).days
        if abs(delta) <= days:
            cand.append((abs(delta), delta, e))
    cand.sort(key=lambda x: (x[0], x[1]))
    return [(delta, e) for _, delta, e in cand[:limit]]


def retrieve_fallback(events, question, k=3):
    r"""兜底检索（工程修复 v6-eng）：字符二元组模糊匹配。
    关键词检索(整词 in)对同义改写过的问题失效时(Q028 类),用 question 的
    2-gram 与事件检索文本的重合度兜底。返回按命中 gram 数排序的 top-k 事件。"""
    q = question or ""
    grams = {q[i:i + 2] for i in range(len(q) - 1)}
    scored = []
    for e in events:
        txt = _search_text(e)
        hits = sum(1 for g in grams if g in txt)
        if hits >= 3:  # 至少 3 个二元组命中,避免噪音
            scored.append((hits, e))
    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    out, seen = [], set()
    for _, e in scored:
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        out.append(e)
        if len(out) >= k:
            break
    return out


def extract_keywords_v2(question):
    """round-6 关键词抽取 v2：引号短语 + 4~6 字内容滑动窗 + 短 token。
    解决旧版长尾整句当关键词、内容短语漏匹配的召回问题(v2 Q110 等)。"""
    q = question or ""
    kws = []
    for pat in ("「([^」]{2,30})」", "【([^】]{2,30})】", "『([^』]{2,30})』"):
        kws.extend(re.findall(pat, q))
    cjk = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", q)  # 去掉标点与空白
    n = len(cjk)
    seen = set()
    for L in (7, 6, 5, 4):
        for i in range(max(n - L + 1, 0)):
            w = cjk[i:i + L]
            if w not in seen:
                seen.add(w)
                kws.append(w)
            if len(kws) >= 70:
                break
        if len(kws) >= 70:
            break
    body = re.sub(r"[「」【】『』（）()，。？！、:：；;，,]", " ", q)
    for w in body.split():
        if 2 <= len(w) <= 10 and w not in kws:
            kws.append(w)
    return kws[:60]


def build_prompt(q, anchor, chain, evs, extra_anchors=None, neighbors=None):
    lines = [f"问题：{q.get('question', '')}"]
    a = anchor or {}
    def _evt_line(x, prefix="锚点事件"):
        b = f" before:{x.get('before')}" if x.get("before") is not None else ""
        af = f" after:{x.get('after')}" if x.get("after") else ""
        return (f"{prefix}：({x.get('ts')}) {x.get('type')}|{x.get('subject')}|{x.get('action')}|{x.get('object')}"
                f"{b}{af}")
    lines.append(_evt_line(a))
    for x in (extra_anchors or []):
        if x.get("id") == a.get("id"):
            continue
        lines.append(_evt_line(x, "相关锚点"))
    # 证据定位题:给出来源出处(corpus#source_hash),并指示按该格式引用
    if (q.get("type") == "证据定位") or ("定位" in (q.get("question") or "")):
        srcs = set()
        for x in [a] + (extra_anchors or []):
            if x and x.get("source_hash"):
                srcs.add(f"[src: {x.get('corpus') or '?'}#{str(x['source_hash'])[:12]}]")
        if srcs:
            lines.append("该变更的出处引用：" + "、".join(sorted(srcs)))
        lines.append("回答提示：本题为「证据定位」，证据行请输出上方给出的出处引用（形如 [src: 群聊4#xxxxxxxxxxxx]），"
                     "不要只写图库事件；若出处引用不可用，再退化到图库事件。")
    if chain:
        # ⚠️ 提示词图例必须与**实际标签值域**一致。
        # 历史生产边集只出现过 strong/weak；自 D-① 闸门（2026-09-22）起，
        # `conf<=0.6` 的入边在库内标为 `pending`（= 不过闸门，语义同 weak：仅线索）。
        # 若图例只说 strong/weak，则 pending 边会**失去图例解释**，
        # 且原先 weak 边随带的「/弱」提示也会消失 ⇒ 实测 7/10 题的 prompt 退化。
        lines.append("证据链（含 strong/weak/pending 边；weak、pending 均为弱关联、仅供参考，不参与多跳）：")
        anchor_ids = {a.get("id") for a in [anchor] + (extra_anchors or []) if a}
        prio = os.environ.get("LLM_CHAIN_PRIORITIZE") == "1"
        for ed in chain:
            f_ = evs.get(ed.get("from_event"), {})
            t_ = evs.get(ed.get("to_event"), {})
            # `pending` 与 `weak` 在**语义**上同为「弱关联线索」⇒ 同样加「/弱」标记，
            # 使闸门改标**不改变提示词给出的语义信号**（只改变状态字面）。
            tag = "/弱" if ed.get("strength") in ("weak", "pending") else ""
            if ed.get("strength") == "pending":
                tag += "（低置信,仅线索）"
            direct = ""
            if prio and (ed.get("from_event") in anchor_ids or ed.get("to_event") in anchor_ids):
                direct = "【直接连锚点】"
            lines.append(
                f"- {ed.get('type')}/{ed.get('strength') or '?'}{tag}{direct}: "
                f"({f_.get('ts')}){f_.get('subject')}{f_.get('action')}{f_.get('object')}"
                f" → ({t_.get('ts')}){t_.get('subject')}{t_.get('action')}{t_.get('object')}"
                f"｜理由:{ed.get('rationale', '')}")
    else:
        lines.append("证据链：空（仅锚点事件可用）")
    if neighbors:
        lines.append("时间邻近事件（同语料±12天内，仅背景参考，未必有因果关系）：")
        for delta, e in neighbors:
            lines.append(f"- [{'+' if delta > 0 else ''}{delta}天] ({e.get('ts')}) "
                         f"{e.get('type')}|{e.get('subject')}|{e.get('action')}|{e.get('object')}"
                         + (f" after:{e.get('after')}" if e.get("after") else ""))
    if os.environ.get("LLM_PROMPT_CAUSE"):  # 工程修复 v6-eng: 动因区分提示
        lines.append("回答提示：「原因」优先回答直接触发因素（已发生的具体事实/问题/数据）,"
                     "而非「为了什么」的目的性动机;两者都要说时,直接触发因素放最前。")
    if os.environ.get("LLM_PROMPT_ORIGIN", "1") != "0":  # round-2 O2: 出处/背景题覆盖变更语境(默认开,置0关)
        lines.append("回答提示：当问题在问某变更的「出处/来源/为什么这样调整」时，"
                     "「原因」除了直接触发因素，还应覆盖该变更所在的背景语境"
                     "（该变更属于哪类体系/主题的调整、出自哪个群聊/哪条工作线的讨论、针对哪个上游改动），"
                     "并在「证据」中优先引用最能定位该变更来源的事件。")
        if os.environ.get("LLM_PROMPT_ORIGIN_EXAMPLE") == "1":
            lines.append("因果表述示例：若档案存在“R90018 修改真实性欠缺标记为行为违规 → R90017 更新质检标签体系”，"
                         "原因应明确写成“R90018 的标记修复推动 R90017 的标签体系更新”，"
                         "不要只写“属于体系调整”或“证据不足”。其他题也按“具体前置事件 → 目标变更”组织原因，"
                         "但只能使用当前证据链中真实存在的事件。")
    if os.environ.get("LLM_PROMPT_EVIDENCE_FIRST") == "1":
        supplied = {}
        for event in [a] + (extra_anchors or []):
            if event.get("id"):
                supplied[event["id"]] = event
        for edge in chain or []:
            for key in ("from_event", "to_event"):
                event = evs.get(edge.get(key), {})
                if event.get("id"):
                    supplied[event["id"]] = event
        for _, event in neighbors or []:
            if event.get("id"):
                supplied[event["id"]] = event
        lines.append("已提供事件的编号与出处对照（只定位上述材料，不增加因果关系）：")
        for event_id, event in supplied.items():
            source = (f" [src: workspace/events #{str(event['source_hash'])[:12]}]"
                      if event.get("source_hash") else "")
            lines.append(f"- {event_id} ({event.get('ts')}) "
                         f"{event.get('subject')}|{event.get('action')}|{event.get('object')}{source}")
        lines.append("先在已提供证据中定位支持结论的真实事件编号和出处，再组织答案。"
                     "最终仅输出原因、结果、证据三行。原因陈述已发生且有证据支持的触发事实；"
                     "不得把目的、时间相邻或弱关联自动升级为因果。结果直接回答目标决策或变化。"
                     "证据优先列当前提供材料中的事件编号与日期，并保留有效来源标识。"
                     "编号不得猜测；不足时说明证据不足。")
    lines.append("请只依据以上信息回答，不编造。严格按以下三行格式输出（每行尽量简短、给出来源时间与编号）：")
    lines.append("原因：<为什么>")
    lines.append("结果：<产生了什么结果/后续动作>")
    lines.append("证据：<引用最关键的事件(时间+主体+对象)，如证据不足请明说>")
    return "\n".join(lines)


def prepare_question(db_path, q, events):
    """Reuse the generation retrieval path without credentials or model calls."""
    evs = {e["id"]: e for e in events}
    kws = (extract_keywords_v2 if os.environ.get("LLM_EXT_V2", "1") != "0"
           else extract_keywords)(q.get("question", ""))
    anchors = retrieve_top(events, kws, k=3, question=q.get("question", ""))
    if os.environ.get("LLM_RETRIEVE_HYBRID", "1") != "0":
        anchors = retrieve_hybrid(events, kws, q.get("question", ""), k=3) or anchors
    fallback_used = 0
    if os.environ.get("LLM_FALLBACK") and not anchors:
        fb = retrieve_fallback(events, q.get("question", ""), k=3)
        if fb:
            anchors = fb
            fallback_used = len(fb)
    chain, seen_ed = [], set()
    for anchor in anchors:
        for edge in chain_recall(db_path, anchor["id"], max_hops=3).get("edges", []):
            pair = (edge.get("from_event"), edge.get("to_event"))
            if pair not in seen_ed:
                seen_ed.add(pair)
                chain.append(edge)
    if os.environ.get("LLM_CHAIN_PRIORITIZE") == "1":
        ids = {a["id"] for a in anchors}
        chain.sort(key=lambda edge: (
            0 if edge.get("from_event") in ids or edge.get("to_event") in ids else 1,
            0 if edge.get("strength") == "strong" else 1,
            -float(edge.get("confidence") or 0),
        ))
    anchor = anchors[0] if anchors else None
    neighbors = []
    if anchor is not None and os.environ.get("LLM_NEIGHBORS"):
        ids = {a.get("id") or a.get("event_id") for a in anchors}
        neighbors = [item for item in temporal_neighbors(events, anchor, days=12, limit=5)
                     if (item[1].get("id") or item[1].get("event_id")) not in ids][:4]
    return {"prompt": build_prompt(q, anchor, chain, evs, anchors[1:], neighbors),
            "keywords": kws, "anchors": anchors, "chain": chain,
            "neighbors": neighbors, "fallback_used": fallback_used}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, "answers-llm.jsonl")

    events = load_events(args.db)
    evs = {e["id"]: e for e in events}
    qs = []
    with io.open(args.questions, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                qs.append(json.loads(line))
    _ = provider_conf()  # 提前校验凭证
    done_qids = set()
    if os.path.exists(out_path):  # 断点续跑
        with io.open(out_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    done_qids.add(json.loads(line)["qid"])
    print(f"[llm-answer] {len(qs)} 题, 已完成 {len(done_qids)}", flush=True)

    with io.open(out_path, "a", encoding="utf-8") as fout:
        for i, q in enumerate(qs):
            if q["qid"] in done_qids:
                continue
            prepared = prepare_question(args.db, q, events)
            kws, anchors = prepared["keywords"], prepared["anchors"]
            chain, neighbors = prepared["chain"], prepared["neighbors"]
            fallback_used, prompt = prepared["fallback_used"], prepared["prompt"]
            t0 = time.time()
            try:
                text = chat([{"role": "user", "content": prompt}])
                ok = True
            except Exception as e:  # noqa: BLE001
                text = f"(生成失败: {e})"
                ok = False
            rec = {
                "qid": q["qid"], "question": q.get("question"),
                "answer_text": text,
                "evidence_chain": [f"[src: workspace/events #{str(e_['source_hash'])[:12]}]"
                                   for ed in chain for e_ in
                                   (evs.get(ed.get("from_event"), {}), evs.get(ed.get("to_event"), {}))
                                   if e_.get("source_hash")],
                "latency_ms": int((time.time() - t0) * 1000),
                "pipeline_ok": ok,
                "trace": f"llm={provider_conf()['model']} kws={kws} anchors={len(anchors)} chain={len(chain)} nb={len(neighbors)}"
                         + (f" fallback={fallback_used}" if fallback_used else ""),
            }
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fout.flush()
            print(f"[{i + 1}/{len(qs)}] {q['qid']} ok={ok} {len(text)}字", flush=True)
            time.sleep(GAP_SECONDS)
    print("[llm-answer] done →", out_path, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
