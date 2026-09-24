# -*- coding: utf-8 -*-
"""按项目功能给 JVS 全量文件做中文分类，生成索引层。
原则：只读物理结构，不移动/改名任何被引用的目录。
输出（本脚本只产前两项；其余由 _migrate/ 下的同族生成器产出）：
  JVS/_index/文件索引.jsonl      逐文件 (路径, 大类, 子类)      ← 本脚本
  JVS/_index/分类统计.json       机器可读统计                  ← 本脚本
  JVS/_index/功能进度表.md       只列进度的精简版              ← make_progress_md.py
  JVS/_index/一次性脚本登记表.md 全库 .py 生命周期              ← make_scripts_ledger.py
  JVS/_index/功能分类总表.md     人类可读总表                  ← make_index_md.py
  JVS/_handoff/PENDING.md        待办 + 已闭合留档              ← make_progress_md.py
  JVS/_handoff/scripts/SCRIPTS.md 脚本登记表                    ← make_scripts_md.py
⚠️ 六个生成器的**执行顺序不可换**，且必须**先 `git add -A` 再跑**（产物内嵌「是否入库」标记）。
   顺序见 README.md §功能索引层 / 技能 jvs-index-layer-maintenance。
"""
import os, io, json, collections

JVS = r"C:\Users\<user>\Desktop\JVS"
IDX = os.path.join(JVS, "_index")

P01 = "01-host-product" + "\\" + "2026-08-28-20-59-40"
P03 = "03-d10-workspace" + "\\" + "2026-09-08-20-30-19"
HMD = P01 + "\\docs\\host_memory_dump"
D10X = "02-m1-evaluation\\outputs\\m1-10-d10-"
P03OUT = P03 + "\\outputs\\"


def has(*subs):
    return lambda p: any(s in p for s in subs)


def starts(*prefixes):
    return lambda p: any(p.startswith(x) for x in prefixes)


def base_is(*names):
    return lambda p: os.path.basename(p) in names


def base_starts(*prefixes):
    return lambda p: any(os.path.basename(p).startswith(x) for x in prefixes)


def in_hmd(extra=None):
    """在 host_memory_dump 下（可选附加条件）"""
    def f(p):
        if not p.startswith(HMD + "\\"):
            return False
        return extra(p) if extra else True
    return f


RULES = [
    # ===== 归档留档（**必须排在最前**）=====
    # `_archive/` 下的文件**保留原始文件名与原始相对路径**（如
    # `_archive/bak-2026-09/03-d10-workspace/.../build_acceptance_ledger.py.bak-20260915-r12`），
    # 因此路径里仍带原分区名与关键词 ⇒ 会被下文若干**未包 in_hmd 的宽泛 `has()` 规则**
    # （如 `has("m1-10-targeted-", ...)`）误判成业务产物。规则顺序即优先级，故置于最前。
    ("09 工程与运维", "归档留档", starts("_archive\\")),

    # ===== 10 受限素材 =====
    ("10 受限素材", "受限语料与验收样本", starts("04-restricted-materials\\")),

    # ===== 09 工程与运维 =====
    ("09 工程与运维", "迁移与索引", lambda p: has("migration-plan-")(p)
        or p.startswith("_index\\") or p.startswith("_migrate\\")),
    ("09 工程与运维", "接手包与技能", starts("_handoff\\")),
    ("09 工程与运维", "门禁与钩子", starts("scripts\\", ".githooks\\")),
    ("09 工程与运维", "导航文档", lambda p: "\\" not in p and base_is(
        "README.md", "MIGRATION-RECORD.md", "PROJECT-OVERVIEW.md")(p)),
    ("09 工程与运维", "仓库配置", base_is(".gitattributes", ".gitignore", "GIT-POLICY.md")),
    # 「项目审计」= 仓库级治理文档（物理整理审计 / 重构方案与阶段判据 / 规范对齐方案），非单模块文档
    # ⚠️ 新增仓库级治理文档时必须同步本行 + status_map.py 的 ("09 工程与运维", "项目审计")，
    #    否则该文档会落到兜底桶 FALLBACK = ("99 未归类", "待人工归位")。
    # ⚠️ 「非预期新增」的兜底检查**不在本文件**（本文件只打印 `扫描物理文件: N` + 各 L1/L2 明细）——
    #    它在 jvs-index-layer-maintenance 技能「校验」节的 `未归类` 检查（`k.startswith('99')`），须自己跑。
    ("09 工程与运维", "项目审计", lambda p: has("project-audit-")(p)
        or base_is("ORGANIZATION-AUDIT.md", "REFACTOR-PLAN.md", "PHASE-C-PLAN.md",
                   "DEV-STANDARDS-ALIGNMENT-PLAN.md")(p)),

    # ===== 裸文档归属（评测类优先于产品定义）=====
    ("04 评测与实验", "回归与指纹复核", base_starts("M1-09-")),
    ("04 评测与实验", "模型对比", base_starts("M1-LLM判边模型对比")),
    ("01 产品定义", "路线图与进度", lambda p: base_starts("M0执行方案", "M1-W1-开工核对单")(p)),

    # ===== 01 产品定义 =====
    ("01 产品定义", "设计主文档", lambda p: p.startswith(P01 + "\\")
        and "jarvis-agent-design" in p and p.count("\\") == 1),
    ("01 产品定义", "设计评审与派工", lambda p: p.startswith(P01 + "\\")
        and "jarvis-agent-design" in p),
    ("01 产品定义", "立项与决策档案", lambda p: base_is(
        "README-版本说明.md", "v0.1勘误对照表.md", "v0.2-复核意见.md",
        "M1-交接包-20260830.md", "M1-W1-全勾确认书-20260830.md")(p)
        or has("M1-立项书-", "\\Gate1-", "\\Gate2-", "M1-任务包-",
               "M1-决策A执行成果", "M1-审计结论")(p)),
    ("01 产品定义", "路线图与进度", has("\\M1-W1-进度", "\\M1-W2-进度", "\\M1-W3-进度",
                                   "\\M1-W3-重判结果", "\\M1-风险登记")),
    ("01 产品定义", "调研与对标", has("侦察报告", "holojarvis", "memU-", "openclaw-",
                                 "openjarvis-", "ecosystem-facts", "生态空白")),
    ("01 产品定义", "链A PoC 与环境成本", has("\\B01-链A-PoC", "\\b01-链A-PoC",
                                        "\\b03-成本复核", "\\b04-端侧常驻", "\\b05-导入与脱敏",
                                        "环境安装记录", "cost-model", "资源与派工约定",
                                        "NVIDIA Corporation")),
    ("01 产品定义", "评测设计口径", has("\\eval-error-memory", "\\eval-qa-")),
    ("01 产品定义", "下一步规划", lambda p: base_starts("next-plan-")(p)),

    # ===== 02 技术实现 =====
    ("02 技术实现", "抽取提示词", base_is("prompt_event_extract.md")),
    ("02 技术实现", "因果判边提示词", base_starts("prompt_causal_edges")),
    ("02 技术实现", "因果图谱引擎", has("\\tools\\causal\\")),
    ("02 技术实现", "评测工具", has("\\tools\\eval\\")),
    ("02 技术实现", "宿主数据采集", has("\\tools\\host\\")),

    # ===== 06 抽取执行 =====
    ("06 抽取执行", "全量提取(100 payload)", lambda p: p.startswith(D10X + "event-extract-full-")),
    ("06 抽取执行", "试点提取", lambda p: p.startswith(D10X + "event-extract-pilot-")),
    ("06 抽取执行", "合成语料试跑", lambda p: p.startswith(P03OUT + "d10-synthetic-")),
    ("06 抽取执行", "重定位沙箱", lambda p: p.startswith(P03OUT + "d10-relocation-")),

    # ===== 07 语义复核机械（必须早于 06/自定义兜底，否则被吞）=====
    ("07 语义复核机械", "验证阶段快照", has("\\_validation-stage-")),
    ("07 语义复核机械", "全量复核修订层", has("\\outputs\\full-review")),
    ("07 语义复核机械", "人工与浏览器复核", has("\\outputs\\browser-review",
                                        "\\outputs\\reviews-manual")),
    ("07 语义复核机械", "零修订审计", has("\\zero-revise-audit")),
    ("07 语义复核机械", "复核暂存与校准", has("\\review-staging", "\\_caliber-scratch",
                                       "\\_scratch-g12-probe", "\\_verify-tmp",
                                       "\\_g05c09_verify_tmp", "\\_g08r4",
                                       "\\_verify-r7", "\\.tmp-g01")),
    ("06 抽取执行", "工作区抽取暂存", starts(P03OUT)),

    # ===== 05 治理与决策（D 系列）=====
    ("05 治理与决策", "D10 交付物(留出题集)", has("m1-10-d10-holdout-", "m1-10-d10-independence-check-")),
    ("05 治理与决策", "D10 范围与候选", has("m1-10-d10-scope-", "m1-10-d10-candidates-",
                                     "m1-10-d10-new-source-intake-", "m1-10-d10-questions-draft-")),
    ("05 治理与决策", "D09 本地样本验收", has("m1-10-d09-")),
    ("05 治理与决策", "证据治理", has("m1-10-evidence-")),
    ("05 治理与决策", "来源链审计", has("m1-10-source-chain-audit")),
    ("05 治理与决策", "定向评测与重跑", has("m1-10-targeted-", "m1-10-rerun-",
                                    "m1-10-source-decisions")),

    # ===== 08 桥接与协作 =====
    ("08 桥接与协作", "DSH 桥接", has("\\host_memory_dump\\bridge")),

    # ===== 03 语料与图谱 =====
    ("03 语料与图谱", "原始素材", in_hmd(has("\\incoming_real\\"))),
    ("03 语料与图谱", "采集原始记录", in_hmd(has("\\m1_02_raws", "m1_02_records"))),
    ("03 语料与图谱", "题集与 gold", in_hmd(lambda p: base_starts("query-set-real")(p)
        or "gold" in os.path.basename(p).lower()
        or has("\\m1_10_baseline_final\\", "\\m1_10_baseline\\")(p))),
    ("03 语料与图谱", "事件库", in_hmd(base_starts("all-real-events"))),
    ("03 语料与图谱", "因果边库", in_hmd(base_starts("all-real-edges"))),
    ("03 语料与图谱", "图谱定版", in_hmd(lambda p: base_starts("causal_graph")(p)
        or "正式图库定版说明" in p)),
    ("03 语料与图谱", "候选对", in_hmd(lambda p: base_starts("candidate-pairs")(p)
        or base_starts("candidates-m1")(p))),
    ("03 语料与图谱", "覆盖度与审计", in_hmd(has("coverage-", "audit-", "\\s3_real", "payload_event_extract"))),

    # ===== 04 评测与实验 =====
    ("04 评测与实验", "评测报告", in_hmd(base_starts("M1-10-", "M1-07-", "M1-正式图库",
                                             "dsh-takeover-", "dsh-"))),
    ("04 评测与实验", "基线实验变体", in_hmd(has("\\m1_10_"))),
    ("04 评测与实验", "评测中间产物", in_hmd()),
    ("04 评测与实验", "评测未归类", starts("02-m1-evaluation\\")),

    # ===== 07 语义复核机械 =====
    ("07 语义复核机械", "台账与校验器", lambda p: p.startswith(P03 + "\\") and (
        "ledger" in os.path.basename(p).lower()
        or "validator" in os.path.basename(p).lower()
        or base_starts("_p0")(p) or base_starts("blocked_authorization")(p))),
    ("07 语义复核机械", "序列化公共模块", lambda p: p.startswith(P03 + "\\tools\\")),
    ("07 语义复核机械", "修订脚本与暂存", starts(P03 + "\\")),

    # ===== 兜底 =====
    ("01 产品定义", "产品定义未归类", starts("01-host-product\\")),
]

FALLBACK = ("99 未归类", "待人工归位")

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".workbuddy-ai"}


def classify(rel):
    for l1, l2, pred in RULES:
        try:
            if pred(rel):
                return l1, l2
        except Exception:
            pass
    return FALLBACK


def main():
    os.makedirs(IDX, exist_ok=True)
    rows = []
    for dp, dn, fn in os.walk(JVS):
        dn[:] = [d for d in dn if not os.path.islink(os.path.join(dp, d))
                 and d not in SKIP_DIRS]
        if os.path.normpath(dp).startswith(os.path.normpath(IDX)):
            dn[:] = []
            continue
        for f in fn:
            fp = os.path.join(dp, f)
            rel = os.path.relpath(fp, JVS)
            if rel.lower().startswith("_migrate\\"):
                continue
            l1, l2 = classify(rel)
            try:
                s = os.path.getsize(fp)
            except OSError:
                s = -1
            rows.append({"p": rel, "l1": l1, "l2": l2, "s": s})

    with io.open(os.path.join(IDX, "文件索引.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    stat = collections.defaultdict(lambda: collections.defaultdict(int))
    for r in rows:
        stat[r["l1"]][r["l2"]] += 1
    stat = {k: dict(v) for k, v in sorted(stat.items())}
    with io.open(os.path.join(IDX, "分类统计.json"), "w", encoding="utf-8") as f:
        json.dump({"total": len(rows), "by_l1_l2": stat}, f, ensure_ascii=False, indent=2)

    print("扫描物理文件: %d" % len(rows))
    print()
    for l1 in sorted(stat):
        print("=== %s  (%d)" % (l1, sum(stat[l1].values())))
        for l2 in sorted(stat[l1], key=lambda x: -stat[l1][x]):
            print("      %-26s %6d" % (l2, stat[l1][l2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())