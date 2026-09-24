# -*- coding: utf-8 -*-
r"""证据排除影响评估（只读）。

合并自 `exclusion_impact.py` / `exclusion_impact2.py` / `exclusion_final.py`。
三个原脚本是**同一问题的三轮追问**：「若排除 `.sse` / `.zip` / 图片，证据链会不会断？」
第一轮按**文件名**判冗余，第二轮改按**内容**判（原脚本自称「修正上一版 C 的匹配逻辑」），
第三轮落到**思考链归属 + 硬断言 + 权威交付包**。它们不是同一脚本的参数化变体
（218 / 182 / 167 行；扫描口径、抽样上限、扩展名集各不相同），故本脚本按**能力（问题）**
拆子命令，不按原脚本拆 —— 后者只是「三个文件塞进一个」。

| 子命令 | 问题 | 原脚本 |
|---|---|---|
| `inventory`     | 待排除文件有多少、多大 | impact1 §0 |
| `dangling`      | 有清单/校验文件点名了待排除文件吗（悬空引用） | impact1 §A |
| `refs-sse`      | 哪些文件引用了 `.sse`，各属什么性质 | impact2 §2 |
| `zip-dir`       | `.zip` 是否只是同名目录的打包副本 | impact1 §B |
| `zip-orphan`    | 无同名目录的 `.zip`，内容在别处存在吗 | impact2 §3 |
| `zip-final-pkg` | 权威交付包 `final-review-package.zip` 有等价目录吗 | final §4 |
| `sse-name`      | `.sse` 有同名 `.json` 承载吗（**名称层**） | impact1 §C |
| `sse-content`   | `payload.response.json` 是否承载 `.sse` 正文（**内容层**） | impact2 §1 |
| `sse-reasoning` | 思考链 `reasoning_content` 是否只存在于 `.sse` | final §1 |
| `code-deps`     | 代码/文档对 `.sse` / `.zip` 的引用清单 | impact1 §D + impact2 §4 |
| `png`           | 图片位置与体积 | impact1 §E + impact2 §5 + final §3 |
| `assert`        | `verify_delivery.py` / `compare_models.py` 的硬断言 | final §2 |
| `all`           | 上表全部 | — |

用法：
    python _migrate/exclusion_audit.py inventory
    python _migrate/exclusion_audit.py sse-content
    python _migrate/exclusion_audit.py --list

⚠️ 只读，不改任何文件。几乎所有子命令都要遍历全库（数 GB），耗时较长；`all` 最慢。
⚠️ 计数类输出是**当时快照**。引用任何数字前请现测（本项目「静默过期」防护）。

---
**合并口径（与原文的差异，逐条可核）**

1. **有意省略的死代码**（原文定义后从未使用，保留只会误导）：
   - `exclusion_impact.py` 的 `in_jvs()`
   - `exclusion_impact.py` 的 `MANIFEST_HINT` —— 原文 §A 虽定义了「清单类文件名」正则，
     实际扫描的是**全部**文本文件，未用它过滤。故「悬空引用」的口径本来就是全库扫描。
   - `exclusion_final.py` §1 的 `sse = os.path.join(...)`（残缺表达式，赋值后未用）
   - `exclusion_final.py` §2 的 `mark`（在 `if j != i - 1` 分支内恒为 `"  "`，且未参与打印）
2. **`png` 三块合一**：三个原脚本输出的是**同一份图片清单**，只是扩展名口径不同
   （`.png/.jpg` → `.png/.jpg/.jpeg` → `.png/.jpg/.jpeg/.gif/.webp`）。本子命令取**并集**、
   按路径排序、只打印**一份**。这是有意的口径收敛：三份内容重叠的清单是噪声，
   而并集对三者均为**超集**（不丢任何一行）。
3. **共享助手**：`walk_jvs()`（原三脚本把「跳过 `.git`/`__pycache__`」那行抄了 8 遍）、
   `read_text()`（原各处的体积门限 + try/except）、`collect_excluded()`、`PAT`、`SSE_RE`。
4. **`collect_excluded()` 进程内缓存**：`all` 下 7 个子命令共用同一次遍历，避免重复走全库。
   单次进程内文件集视为一致（只读审计场景成立）。
"""
import argparse
import collections
import glob
import json
import os
import re
import sys
import zipfile

JVS = r"C:\Users\<user>\Desktop\JVS"
SEP = "=" * 74

# 待排除扩展名（impact1 原文口径）
EXCL_EXT = {".sse", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
            ".pyc", ".pyo", ".log", ".tmp", ".bak", ".orig", ".rej"}
# 可读文本类扩展名（impact1 §A / impact2 §2 口径）
TEXT_EXT = (".json", ".jsonl", ".md", ".txt", ".csv")
# 代码/文档引用扫描（impact1 §D / impact2 §4 共用）
CODE_EXT = (".py", ".cjs", ".sh", ".md", ".js")
PAT = re.compile(r"\.(sse|zip)\b")
# payload 三元组命名规律
SSE_RE = re.compile(r"^(.*?)\.payload\.stream-response-[0-9a-f]+\.sse$")


def rel(p):
    return os.path.relpath(p, JVS)


def walk_jvs():
    """遍历 JVS，跳过 `.git` / `__pycache__`。

    原三个脚本把这一行过滤抄了 8 遍，此处收敛为一处。
    """
    for root, dirs, files in os.walk(JVS):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for f in files:
            yield root, f


def read_text(p, max_mb=8):
    """按体积门限读文本；超限或失败返回 None。"""
    try:
        if os.path.getsize(p) > max_mb * 1024 * 1024:
            return None
        return open(p, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        return None


_EXCLUDED_CACHE = []


def collect_excluded():
    """待排除文件清单 `[(path, size)]`（进程内缓存，见模块头「合并口径 4」）。"""
    if _EXCLUDED_CACHE:
        return _EXCLUDED_CACHE[0]
    out = []
    for root, f in walk_jvs():
        if os.path.splitext(f)[1].lower() in EXCL_EXT:
            p = os.path.join(root, f)
            try:
                sz = os.path.getsize(p)
            except Exception:
                sz = 0
            out.append((p, sz))
    _EXCLUDED_CACHE.append(out)
    return out


# --------------------------------------------------------------------------
# impact1 §0 —— 待排除文件总览
# --------------------------------------------------------------------------

def cmd_inventory():
    print(SEP)
    print("0. 待排除文件总览")
    print(SEP)
    excluded = collect_excluded()
    by_ext = collections.Counter()
    by_ext_b = collections.Counter()
    for p, sz in excluded:
        e = os.path.splitext(p)[1].lower()
        by_ext[e] += 1
        by_ext_b[e] += sz
    for e in sorted(by_ext, key=lambda x: -by_ext_b[x]):
        print(f"  {e:<8} {by_ext[e]:>6} 个   {by_ext_b[e]/1048576:>9.2f} MB")
    print(f"  {'合计':<8} {len(excluded):>6} 个   {sum(s for _, s in excluded)/1048576:>9.2f} MB")


# --------------------------------------------------------------------------
# impact1 §A + impact2 §2 —— 悬空引用 / 引用者性质
# --------------------------------------------------------------------------

def cmd_dangling():
    print(SEP)
    print("A. 悬空引用检测：清单/校验/索引文件是否点名了待排除文件")
    print(SEP)
    names = {os.path.basename(p) for p, _ in collect_excluded()}
    scanned = 0
    dangling = collections.defaultdict(list)
    for root, f in walk_jvs():
        if os.path.splitext(f)[1].lower() not in TEXT_EXT:
            continue
        p = os.path.join(root, f)
        txt = read_text(p, 8)
        if txt is None:
            continue
        scanned += 1
        for nm in names:
            if nm in txt:
                dangling[nm].append(os.path.relpath(p, JVS))
    print(f"  扫描文本文件 {scanned} 个")
    if dangling:
        print(f"  有 {len(dangling)} 个待排除文件名被引用：")
        for nm in sorted(dangling, key=lambda x: -len(dangling[x]))[:25]:
            print(f"    {nm}  <- {len(dangling[nm])} 处引用")
            for src in dangling[nm][:3]:
                print(f"         {src}")
    else:
        print("  无任何待排除文件被点名  -> 零悬空引用")


def cmd_refs_sse():
    print(SEP)
    print("2. 引用 .sse 的清单/校验文件（断链风险评估）")
    print(SEP)
    sse_names = {os.path.basename(p) for p, _ in collect_excluded()
                 if p.lower().endswith(".sse")}
    refs = collections.defaultdict(set)
    for root, f in walk_jvs():
        if os.path.splitext(f)[1].lower() not in TEXT_EXT:
            continue
        p = os.path.join(root, f)
        txt = read_text(p, 8)
        if txt is None:
            continue
        for nm in sse_names:
            if nm in txt:
                refs[rel(p)].add(nm)
    print(f"  引用 .sse 的文件数: {len(refs)}")
    by_kind = collections.Counter()
    for f, names in refs.items():
        b = os.path.basename(f)
        if b.startswith("s1-full-manifest"):
            k = "① 我的迁移清单(非业务)"
        elif b == "integrity.json":
            k = "② 交付完整性校验"
        elif b.endswith(".meta.json"):
            k = "③ 单批元数据"
        elif b.endswith(".response.json"):
            k = "④ 模型响应件"
        elif b.endswith(".txt"):
            k = "⑤ 文本产物"
        else:
            k = "⑥ 其他"
        by_kind[k] += 1
    for k in sorted(by_kind):
        print(f"    {k:<28} {by_kind[k]:>5} 个文件")
    print()
    print("  按文件列出（前 40）:")
    for f in sorted(refs, key=lambda x: -len(refs[x]))[:40]:
        print(f"    {len(refs[f]):>4} 引用  {f}")

    print()
    print("  integrity.json 样例:")
    for f in refs:
        if os.path.basename(f) == "integrity.json":
            p = os.path.join(JVS, f)
            try:
                d = json.load(open(p, encoding="utf-8"))
                print(f"    文件: {f}")
                print(f"    顶层键: {list(d)[:12] if isinstance(d, dict) else type(d)}")
                if isinstance(d, dict):
                    for k in list(d)[:4]:
                        v = d[k]
                        s = str(v)
                        print(f"      {k}: {s[:160]}")
            except Exception as e:
                print(f"    !! {f}: {e}")
            break


# --------------------------------------------------------------------------
# impact1 §B + impact2 §3 + final §4 —— .zip 等价性
# --------------------------------------------------------------------------

def cmd_zip_dir():
    print(SEP)
    print("B. .zip 与同名目录等价性（zip 是不是仅打包副本）")
    print(SEP)
    zips = [(p, s) for p, s in collect_excluded() if p.lower().endswith(".zip")]
    same_dir = 0
    no_dir = []
    for p, s in zips:
        stem = p[:-4]
        if os.path.isdir(stem):
            same_dir += 1
        else:
            no_dir.append(os.path.relpath(p, JVS))
    print(f"  .zip 总数 {len(zips)}；存在同名目录的 {same_dir} 个")
    print(f"  无同名目录的 {len(no_dir)} 个：")
    for x in no_dir[:15]:
        print(f"    {x}")
    print()
    print("  抽样比对 zip 内容 vs 同名目录内容（前 6 个）:")
    checked = 0
    for p, s in zips:
        stem = p[:-4]
        if not os.path.isdir(stem):
            continue
        try:
            with zipfile.ZipFile(p) as z:
                zlist = [n for n in z.namelist() if not n.endswith("/")]
        except Exception as e:
            print(f"    !! 读取失败 {os.path.relpath(p, JVS)}: {e}")
            continue
        dcount = sum(len(fs) for _, _, fs in os.walk(stem))
        print(f"    zip内 {len(zlist):>5} 文件 | 目录内 {dcount:>5} 文件 | {os.path.relpath(p, JVS)}")
        checked += 1
        if checked >= 6:
            break


def cmd_zip_orphan():
    print(SEP)
    print("3. 无同名目录的 .zip：内容是否在别处存在")
    print(SEP)
    zips = [p for p, _ in collect_excluded() if p.lower().endswith(".zip")]
    noadir = [p for p in zips if not os.path.isdir(p[:-4])]
    print(f"  .zip 总数 {len(zips)}；无同名目录 {len(noadir)}")
    for p in noadir:
        try:
            with zipfile.ZipFile(p) as z:
                names = [n for n in z.namelist() if not n.endswith("/")]
        except Exception as e:
            print(f"    !! {rel(p)}: {e}")
            continue
        d = os.path.dirname(p)
        found = 0
        for n in names[:200]:
            cand = os.path.join(d, n.replace("/", os.sep))
            if os.path.exists(cand):
                found += 1
        print(f"    {len(names):>5} 内文件 | 同目录下可定位 {found:>4} | {rel(p)}")


def cmd_zip_final_pkg():
    print(SEP)
    print("4. 权威交付包 final-review-package.zip 的等价目录")
    print(SEP)
    for root, f in walk_jvs():
        if f != "final-review-package.zip":
            continue
        p = os.path.join(root, f)
        d = p[:-4]
        n_zip = n_dir = 0
        try:
            with zipfile.ZipFile(p) as z:
                n_zip = len([x for x in z.namelist() if not x.endswith("/")])
        except Exception:
            n_zip = -1
        if os.path.isdir(d):
            n_dir = sum(len(fs) for _, _, fs in os.walk(d))
        print(f"  zip {os.path.getsize(p):>9} B / {n_zip} 内文件 | 同名目录 {n_dir} 文件")
        print(f"    {rel(p)}")
        if os.path.isdir(d):
            for x in sorted(os.listdir(d))[:12]:
                xp = os.path.join(d, x)
                sz = os.path.getsize(xp) if os.path.isfile(xp) else -1
                print(f"      {sz:>10} B  {x}")


# --------------------------------------------------------------------------
# impact1 §C + impact2 §1 + final §1 —— .sse 冗余性
# --------------------------------------------------------------------------

def cmd_sse_name():
    print(SEP)
    print("C. .sse 冗余性：模型流式原文是否被 .json 承载")
    print(SEP)
    sses = [(p, s) for p, s in collect_excluded() if p.lower().endswith(".sse")]
    stem_count = 0
    payload_ok = 0
    sample = sses[:400]
    for p, s in sample:
        base = os.path.basename(p).replace(".sse", "")
        stem_count += 1
        cand = os.path.join(os.path.dirname(p), base + ".json")
        # 也试 去掉 stream-response 后缀
        if not os.path.exists(cand):
            stem2 = re.sub(r"\.stream-response-[0-9a-f]+$", "", base)
            cand = os.path.join(os.path.dirname(p), stem2 + ".json")
        if os.path.exists(cand):
            payload_ok += 1
    print(f"  抽样 {stem_count} 个 .sse；能找到同名 .json 承载的 {payload_ok} 个 "
          f"({payload_ok*100.0/max(stem_count,1):.1f}%)")
    if sses:
        p0, s0 = sses[0]
        print(f"  样例: {os.path.relpath(p0, JVS)}  ({s0/1024:.0f} KB)")
        d = os.path.dirname(p0)
        base = os.path.basename(p0).replace(".sse", "")
        sibs = [f for f in os.listdir(d) if base.split(".")[0] in f]
        for x in sorted(sibs)[:12]:
            fp = os.path.join(d, x)
            print(f"      {os.path.getsize(fp):>9} B  {x}")


def cmd_sse_content():
    print(SEP)
    print("1. .sse 真实冗余性：同目录 payload.response.json 是否承载内容")
    print(SEP)
    sses = [p for p, _ in collect_excluded() if p.lower().endswith(".sse")]
    print(f"  .sse 总数: {len(sses)}")

    ok = 0
    noresp = []
    resp_bytes = sse_bytes = 0
    sample_detail = None
    for p in sses:
        d = os.path.dirname(p)
        m = SSE_RE.match(os.path.basename(p))
        if not m:
            noresp.append(p)
            continue
        prefix = m.group(1)
        rj = os.path.join(d, prefix + ".payload.response.json")
        rl = os.path.join(d, prefix + ".payload.result.txt")
        if os.path.exists(rj):
            ok += 1
            try:
                resp_bytes += os.path.getsize(rj)
                sse_bytes += os.path.getsize(p)
            except Exception:
                pass
            if sample_detail is None:
                sample_detail = (p, rj, rl if os.path.exists(rl) else None)
        else:
            noresp.append(p)

    print(f"  能定位同名 payload.response.json 的: {ok} / {len(sses)} "
          f"({ok*100.0/max(len(sses),1):.1f}%)")
    print(f"  无法定位的: {len(noresp)}")
    for p in noresp[:10]:
        print(f"     {rel(p)}")
    if sample_detail:
        p, rj, rl = sample_detail
        print()
        print("  样例证据链（同一 chunk 的四个文件）:")
        d = os.path.dirname(p)
        pre = os.path.basename(p).split(".payload.")[0]
        for f in sorted(os.listdir(d)):
            if f.startswith(pre + "."):
                print(f"     {os.path.getsize(os.path.join(d,f)):>10} B  {f}")
        print()
        # 验证 response.json 里是否含 SSE 的正文（取 sse 最后一条 data 的 content）
        try:
            sse_txt = open(p, "r", encoding="utf-8", errors="ignore").read()
            contents = re.findall(r'"content":"((?:[^"\\]|\\.)*)"', sse_txt)
            tail = contents[-200:] if contents else []
            joined = "".join(tail)
            resp_txt = open(rj, "r", encoding="utf-8", errors="ignore").read()
            # 取 response.json 中的正文
            m2 = re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', resp_txt)
            resp_body = json.loads('"' + (m2.group(1) if m2 else "") + '"')
            probe = joined[-400:] if len(joined) > 400 else joined
            probe_clean = probe.replace("\\n", "\n")
            covered = probe_clean[:200] in resp_body if probe_clean else False
            print(f"  .sse 尾部正文片段是否出现在 response.json: {covered}")
            print(f"     sse 尾部样本: {repr(probe_clean[-120:])}")
            print(f"     response.json 长度: {len(resp_body)} 字符; 结尾: {repr(resp_body[-120:])}")
        except Exception as e:
            print(f"  内容比对失败: {e}")


def cmd_sse_reasoning():
    print(SEP)
    print("1. 思考链（reasoning_content）归属：只在 .sse 还是 response.json 也有？")
    print(SEP)
    P = os.path.join(JVS, r"02-m1-evaluation\outputs\m1-10-d10-event-extract-full-20260908\delivery-pro-r1\results")
    if not os.path.isdir(P):
        P = os.path.join(JVS, r"02-m1-evaluation\outputs\m1-10-d10-event-extract-pilot-20260908\results")
    stem = None
    for f in sorted(os.listdir(P)):
        if f.endswith(".sse"):
            stem = f.split(".payload.")[0]
            break
    print(f"  样例 batch: {stem}")
    for suf in (".payload.meta.json", ".payload.response.json", ".payload.result.txt"):
        p = os.path.join(P, stem + suf)
        if os.path.exists(p):
            txt = open(p, "r", encoding="utf-8", errors="ignore").read()
            has_r = "reasoning_content" in txt
            has_think = "思考" in txt or "thinking" in txt or "reasoning" in txt
            print(f"  {os.path.getsize(p):>9} B  reasoning_content={has_r:<5} reasoning字样={has_think:<5} {stem+suf}")
        else:
            print(f"  (缺) {stem+suf}")

    # 直接看 .sse 里的 reasoning_content
    cand = glob.glob(os.path.join(P, stem + ".payload.stream-response-*.sse"))
    if cand:
        s = cand[0]
        n = 0
        with open(s, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if "reasoning_content" in line:
                    n += 1
        print(f"  .sse 中含 reasoning_content 的行数: {n}  ({os.path.basename(s)})")
        # 看 response.json 的键结构
        rj = os.path.join(P, stem + ".payload.response.json")
        if os.path.exists(rj):
            try:
                d = json.load(open(rj, encoding="utf-8"))
                if isinstance(d, dict):
                    print(f"  response.json 顶层键: {list(d)[:14]}")

                    def walk(o, path="", depth=0):
                        if depth > 2:
                            return
                        if isinstance(o, dict):
                            for k, v in list(o.items())[:14]:
                                if "reason" in k.lower() or "think" in k.lower():
                                    print(f"     !! 发现思考字段: {path}.{k}")
                                walk(v, path + "." + k, depth + 1)
                        elif isinstance(o, list) and o:
                            walk(o[0], path + "[0]", depth + 1)

                    walk(d)
                    # 看 content 长度 vs 是否有多段
                    c = d.get("content")
                    if isinstance(c, str):
                        print(f"  response.json content 长度: {len(c)}")
                        print(f"    前 160: {repr(c[:160])}")
            except Exception as e:
                print(f"  response.json 解析失败: {e}")

    print()
    print("  --- 跨多个样例统计 reasoning_content 的载体 ---")
    only_sse = both = neither = 0
    checked = 0
    for root, f in walk_jvs():
        if not f.endswith(".sse"):
            continue
        p = os.path.join(root, f)
        s_txt = read_text(p, 6)
        if s_txt is None:
            continue
        m = SSE_RE.match(f)
        if not m:
            continue
        rj = os.path.join(root, m.group(1) + ".payload.response.json")
        if not os.path.exists(rj):
            continue
        r_txt = open(rj, "r", encoding="utf-8", errors="ignore").read()
        s_has = "reasoning_content" in s_txt
        r_has = "reasoning" in r_txt or "thinking" in r_txt
        if s_has and not r_has:
            only_sse += 1
        elif s_has and r_has:
            both += 1
        elif not s_has and not r_has:
            neither += 1
        checked += 1
        if checked >= 300:
            break
    print(f"  抽样 {checked} 组:")
    print(f"    思考链仅存在于 .sse（排掉就丢）: {only_sse}")
    print(f"    .sse 与 response.json 都有     : {both}")
    print(f"    两者都无                       : {neither}")


# --------------------------------------------------------------------------
# impact1 §D + impact2 §4 —— 代码依赖
# --------------------------------------------------------------------------

def cmd_code_deps():
    print(SEP)
    print("D. 代码中对 .sse / .zip 的读取依赖（克隆后是否跑不起来）")
    print(SEP)
    hits = []
    for root, f in walk_jvs():
        if os.path.splitext(f)[1].lower() not in (".py", ".cjs", ".sh", ".md"):
            continue
        p = os.path.join(root, f)
        try:
            if os.path.getsize(p) > 4 * 1024 * 1024:
                continue
            for i, line in enumerate(open(p, "r", encoding="utf-8", errors="ignore"), 1):
                if PAT.search(line):
                    hits.append((os.path.relpath(p, JVS), i, line.strip()[:110]))
        except Exception:
            continue
    print(f"  命中 {len(hits)} 行：")
    for f, i, ln in hits[:30]:
        print(f"    {f}:{i}")
        print(f"        {ln}")

    print()
    print(SEP)
    print("4. 代码/文档中对 .sse / .zip 的引用")
    print(SEP)
    hits2 = collections.defaultdict(list)
    for root, f in walk_jvs():
        if os.path.splitext(f)[1].lower() not in CODE_EXT:
            continue
        p = os.path.join(root, f)
        try:
            if os.path.getsize(p) > 4 * 1024 * 1024:
                continue
            for i, line in enumerate(open(p, "r", encoding="utf-8", errors="ignore"), 1):
                if PAT.search(line):
                    hits2[rel(p)].append((i, line.strip()[:100]))
        except Exception:
            continue
    print(f"  涉及文件 {len(hits2)} 个，共 {sum(len(v) for v in hits2.values())} 行")
    for f in sorted(hits2, key=lambda x: -len(hits2[x]))[:18]:
        print(f"    --- {f}  ({len(hits2[f])} 行)")
        for i, ln in hits2[f][:6]:
            print(f"        :{i}  {ln}")


# --------------------------------------------------------------------------
# impact1 §E + impact2 §5 + final §3 —— 图片（并集口径，见模块头「合并口径 2」）
# --------------------------------------------------------------------------

IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def cmd_png():
    print(SEP)
    print("E. png 的位置与用途（判断是否属于证据）")
    print(SEP)
    print("  口径: 扩展名取并集 %s（原三脚本分别为 .png/.jpg、.png/.jpg/.jpeg、"
          "含 .gif/.webp）" % "/".join(IMG_EXT))
    pngs = [(p, s) for p, s in collect_excluded()
            if os.path.splitext(p)[1].lower() in IMG_EXT]
    for p, s in sorted(pngs):
        print(f"  {s:>9} B  {os.path.relpath(p, JVS)}")


# --------------------------------------------------------------------------
# final §2 —— 硬断言
# --------------------------------------------------------------------------

ASSERT_TARGETS = [
    r"03-d10-workspace\2026-09-08-20-30-19\outputs\verify_delivery.py",
    r"03-d10-workspace\2026-09-08-20-30-19\outputs\compare_models.py",
]


def cmd_assert():
    print(SEP)
    print("2. verify_delivery.py / compare_models.py 对 .sse 的断言性质")
    print(SEP)
    for f in ASSERT_TARGETS:
        p = os.path.join(JVS, f)
        print(f"  --- {f}")
        lines = open(p, encoding="utf-8", errors="ignore").read().splitlines()
        for i, ln in enumerate(lines, 1):
            if ".sse" in ln or ".zip" in ln:
                lo = max(0, i - 4)
                hi = min(len(lines), i + 4)
                print(f"    行 {i}: {ln.strip()}")
                for j in range(lo, hi):
                    if j != i - 1:
                        print(f"      {j+1:>4} {lines[j]}")
                print()


# --------------------------------------------------------------------------

CASES = {
    "inventory":     ("待排除文件总览", cmd_inventory, "impact1 §0"),
    "dangling":      ("悬空引用检测", cmd_dangling, "impact1 §A"),
    "refs-sse":      ("引用 .sse 的文件性质", cmd_refs_sse, "impact2 §2"),
    "zip-dir":       (".zip 与同名目录等价性", cmd_zip_dir, "impact1 §B"),
    "zip-orphan":    ("无同名目录 .zip 的内容定位", cmd_zip_orphan, "impact2 §3"),
    "zip-final-pkg": ("权威交付包等价目录", cmd_zip_final_pkg, "final §4"),
    "sse-name":      (".sse 冗余性（名称层）", cmd_sse_name, "impact1 §C"),
    "sse-content":   (".sse 冗余性（内容层）", cmd_sse_content, "impact2 §1"),
    "sse-reasoning": ("思考链归属", cmd_sse_reasoning, "final §1"),
    "code-deps":     ("代码/文档引用清单", cmd_code_deps, "impact1 §D + impact2 §4"),
    "png":           ("图片位置与体积（并集口径）", cmd_png, "impact1 §E + impact2 §5 + final §3"),
    "assert":        ("硬断言性质", cmd_assert, "final §2"),
}

ALL_ORDER = list(CASES)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="证据排除影响评估（合并自 exclusion_impact{,2} + exclusion_final）")
    ap.add_argument("cmd", nargs="?", help="子命令名（见 --list）")
    ap.add_argument("--list", action="store_true", help="列出全部子命令")
    args = ap.parse_args(argv)

    if args.list or not args.cmd:
        print("可用子命令（共 %d 个，另加 all）：" % len(CASES))
        for name, (title, _fn, origin) in CASES.items():
            print("  %-16s %-30s ← %s" % (name, title, origin))
        return 0 if args.list else 3

    if args.cmd == "all":
        for name in ALL_ORDER:
            if name != ALL_ORDER[0]:
                print()
            CASES[name][1]()
        return 0

    if args.cmd not in CASES:
        print("未知子命令：%s（用 --list 查看）" % args.cmd, file=sys.stderr)
        return 3

    CASES[args.cmd][1]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
