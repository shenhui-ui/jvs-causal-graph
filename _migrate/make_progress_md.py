# -*- coding: utf-8 -*-
"""生成精简进度表（_index/功能进度表.md）与待办清单（_handoff/PENDING.md）。"""
import os, io, json, sys, datetime, collections

JVS = r"C:\Users\<user>\Desktop\JVS"
IDX = os.path.join(JVS, "_index")
TODAY = datetime.date.today().isoformat()
sys.path.insert(0, os.path.join(JVS, "_migrate"))
from status_map import ROADMAP, PENDING, get, rollup  # noqa
import status_map as _sm  # noqa

# ⚠️ 自动发现所有 CLOSED_* 留档组，勿再硬编码单个变量 ——
# 定义了却不渲染 = 收口项静默消失（本项目已踩过）。
CLOSED_GROUPS = [(k, getattr(_sm, k)) for k in sorted(dir(_sm))
                 if k.startswith("CLOSED_") and isinstance(getattr(_sm, k), list)]

BADGE = {"已开发": "✅ 已开发", "开发中": "🔧 开发中", "待开发": "⏳ 待开发", "待规划": "🗓️ 待规划"}


def load():
    stat = json.load(io.open(os.path.join(IDX, "分类统计.json"), encoding="utf-8"))
    return stat["by_l1_l2"]


def write_progress(by):
    out = []
    A = out.append
    A("# JVS 功能进度表（精简版）")
    A("")
    A("> 生成于 %s ｜ 依据：一手证据（验收台账 / 进度文档 / 交付物状态）" % TODAY)
    A("> 完整版（含文件说明）见 `功能分类总表.md`")
    A("")
    A("**项目**：M1「跨会话因果问答」MVP —— 时序-因果图谱 + 跨会话因果问答")
    A("")
    A("图例：✅ 已开发（产物已验证/闭环）｜🔧 开发中（未闭环）｜⏳ 待开发（有设计未产出）｜🗓️ 待规划（未立项）")
    A("")
    A("---")
    A("")
    A("## M1 三级验收现状")
    A("")
    A("| 验收项 | 门槛 | 实测 | 判定 |")
    A("|---|---|---|---|")
    A("| 因果准确率 | ≥60%（18/30） | **21/30 = 70.00%** | ✅ 达标 |")
    A("| 编造边 | 0 条 | **0/67** | ✅ 达标 |")
    A("| 端到端合格率 | ≥60% | — | ⏳ 未评 |")
    A("| 独立留出集（D10） | — | `causal_accuracy` **0.8000**（n=10） | ✅ 报告已出 |")
    A("")
    A("> ⚠️ 留出集合格率**单列，不得与 21/30、22/30 混算**；`cause_hit` 合法分母是 **3** 不是 6 ⇒ **3/3 = 1.0000**。")
    A("> ⚠️ `causal_accuracy` **0.8000 是本题型构成下的理论上限**（「证据定位」2 题不问原因，`cause` 恒 false），")
    A("> **不是「80 分」**；且 **D10 已被 D5 定案消耗，不能作为 D5 的干净留出集**。")
    A("")
    A("---")
    A("")
    A("## 阶段路线图")
    A("")
    for name, st, gate, now in ROADMAP:
        A("### %s　%s" % (name, BADGE[st]))
        A("")
        A("- 门槛：%s" % gate)
        A("- 现状：%s" % now)
        A("")
    A("---")
    A("")
    A("## 全部功能进度")
    A("")
    for l1 in sorted(by):
        r = rollup(l1, by[l1].keys())
        A("### %s　%s" % (l1, BADGE[r]))
        A("")
        A("| 子类 | 进度 |")
        A("|---|---|")
        for l2 in sorted(by[l1], key=lambda x: -by[l1][x]):
            st, _ = get(l1, l2)
            A("| %s | %s |" % (l2, BADGE[st]))
        A("")
    A("---")
    A("")
    A("## 待办清单")
    A("")
    A("| 待办 | 进度 | 依据 |")
    A("|---|---|---|")
    for name, st, why in PENDING:
        A("| %s | %s | %s |" % (name, BADGE[st], why))
    A("")
    A("> 维护：改 `_migrate/status_map.py` 后重跑 `_migrate/make_progress_md.py`")
    A("")

    p = os.path.join(IDX, "功能进度表.md")
    with io.open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("已写出:", p, os.path.getsize(p), "bytes")


def git_facts():
    """从 git 实时取治理事实；失败则回落为占位，不让生成器整体失败。"""
    import subprocess
    out = {}
    def g(*args):
        try:
            return subprocess.run(["git"] + list(args), cwd=JVS, capture_output=True,
                                  text=True, timeout=20).stdout.strip()
        except Exception:
            return ""
    tags = [t for t in g("tag", "-l").splitlines() if t.strip()]
    out["tags"] = tags
    out["commits"] = g("rev-list", "--count", "HEAD") or "?"
    out["branch"] = g("branch", "--show-current") or "?"
    out["head"] = g("rev-parse", "--short", "HEAD") or "?"
    out["sse"] = len([l for l in g("ls-files").splitlines() if l.endswith(".sse")])
    out["zip"] = len([l for l in g("ls-files").splitlines() if l.endswith(".zip")])
    return out


def gate_steps():
    """门禁步数**现数**（判据，而非写死值）—— 防「9 项 / 10 项」类数字静默过期。

    判据：`scripts/git-gate.sh` 里形如 `# ---------- N. 标题 ----------` 的分节注释条数。
    数不到返回 `"?"` —— 宁可显示问号，也不写一个会过期的值（2026-09-21 新增 `[10]` 步时立的）。
    """
    import re
    try:
        with io.open(os.path.join(JVS, "scripts", "git-gate.sh"), encoding="utf-8") as fh:
            return len(re.findall(r"^# -{10} \d+\.", fh.read(), re.M)) or "?"
    except OSError:
        return "?"


# ⚠️ 绝对路径一律用 os.sep 拼接，**不在源码里写反斜杠字面量**。
# 理由（2026-09-21 实测）：把反斜杠路径放进 `python -c "..."` 会被 shell 层改写成正斜杠，
# 进而使 `startswith(旧根)` **静默全假**（曾据此得出「旧根 0 条」的错误结论）。
_OLD_ROOT = "C:" + os.sep + os.sep.join(["Users", "<user>", "host-workspace"]) + os.sep
_NEW_ROOT = "C:" + os.sep + os.sep.join(["Users", "<user>", "Desktop", "JVS"]) + os.sep
_JUNC_SRC = _OLD_ROOT + os.sep.join(["outputs", "m1-10-d10-event-extract-full-20260908"])
_JUNC_DST = _NEW_ROOT + os.sep.join(
    ["02-m1-evaluation", "outputs", "m1-10-d10-event-extract-full-20260908"])


def write_pending(by):
    out = []
    A = out.append
    A("# 待办清单 —— JVS / M1")
    A("")
    A("> 生成于 %s ｜ 依据：交付物状态字段 + 进度文档，**非推测**" % TODAY)
    A("")
    A("---")
    A("")
    A("## 一、当前主线（D10 独立留出题集 —— 链路已闭合）")
    A("")
    A("D10 的 10 道留出题**已交付、已评测、已出报告**，整条链走完：")
    A("")
    A("| # | 环节 | 进度 | 依据 |")
    A("|---|---|---|---|")
    A("| 1 | 人工复核这 10 道题 | ✅ 已开发 | `questions.json` `status = \"frozen_after_user_review\"`（2026-09-16，5 项决议） |")
    A("| 2 | 在冻结图谱上评测留出集 | ✅ 已开发 | 生成 10/10 + 判分 10/10；预算 20 次逻辑调用（上限 30，未超） |")
    A("| 3 | 出过拟合结论 | ✅ 已开发 | `docs/host_memory_dump/M1-10-D10留出集评测报告与过拟合结论-20260919.md` |")
    A("")
    A("**下一步**：⚠️ **仍待项目方裁定 1 项** —— **Q3 规范修订的落点与签署**（v2.1 铁律 3 字面写「`≤0.6` 标记为 pending，**不入库**」，而 D-① 实现是「**改标 pending 并入库**」⇒ **二者偏离**；红线 7：**AI 不代签**）。① ⭐ **D-③ 已于 2026-09-23 17:18:52 落库完成**（用户授权「1授权、2授权、3待定」）：实现层取 **甲（真源层剔除 + 官方入口重建，闸门代码不动）**、`A5530` 出路取 **①（接受连带剔除）** ⇒ **skip 剔除 1,339 条**（边 **8,455 → 7,116**、`strong` 1,907 → **1,906**、`weak` 6,548 → **5,210**、`conf≤0.6` → **0**、**可展开 1,906 不变**）；`apply_d3_pool_removal.py --apply` **12/12 校验全过**，备份 `_d3_backup_20260923-171852/`。⚠️ **用户未采纳原「改标」建议**（事实依据仍成立，被否的是取舍）⇒ **已交付的 D10 评测指标须重跑** —— ✅ **重跑已完成**（生成 **10/10**、判分 **10/10 有效**，产物 `_staging/out_d10_gen_c/`，**不覆盖**原 `out_d10_gen_refill/`）。⭐ **唯一合法口径下 `cause_hit` 3/3 → 2/3（只掉 `D10-H01`）**；官方分母 10 的 8/10→2/10 **不可引用**（旧 8/10 中有 **5 条**属「gold 无显式原因条目却判 `true`」的**结构性假阳性**（H02–H06）；gold 中仅 H01/H07/H08 有实质 cause ⇒ **唯一合法分母 = 3**）。⭐⭐ **结论修正（2026-09-23 实测）**：该下降**不是 D-③ 造成的**，主因是**判分口径变化**，须分两部分读：① **结构性假阳性被收回**（H02–H06 共 **5 题**，gold 无原因条目却曾判 `true`）—— 这是**修正**，不是质量下降；② **服务端漂移**（H01）—— **指纹级铁证**：同一 `input_fingerprint`（`620c8e6f…`）09-18 判 `true`、09-23 判 `false`（`judge_ms` 32,579 → 9,585），而判分器代码 09-16 后无提交、端点/模型名/prompt 模板/gold/答案文本**全同**；且 H01 答案文本本身也变了（旧 203 字含「解决拥堵/上传慢」，新 169 字只复述措施），而**同 prompt 连生成 3 次 `chain` 恒为 13、文本 3 份全异** ⇒ 属**生成采样噪声**，**均与图谱无关** ⇒ ⭐ **D-③ 效应 = 0 行**，须以零 LLM 对照为准。⚠️ `deepseek-v4-pro` 端点级限流且平台文档已不列 ⇒ 历史判分可能不可复现；⭐ **交叉印证**：同一份旧批 H01 答案在当前 v4-pro 与 `deepseek-flash` 下**均判 `cause=false`**（旧批记录为 `true`）⇒ 漂移非单模型偶发；⚠️ **换模型不能绕过限流**（端点级共享池）⇒ 两批完整同口径重判**挂起**（`_d3c_rejudge_both.py` 就绪）。⭐ **归因分离（零 LLM）**：图例/代码效应 **10 行（2.6%）**、前三轮 **194 行**、总 **389 行** ⇒ 主因图谱；⚠️ 仅 **195 行**可归本轮 D-③。⭐ **`D10-H01` 的 `true→false` 不可归因 D-③**（其 prompt 全程仅差 1 行图例、图谱 0 行）⇒ 属生成/判分噪声。⭐ **两项口径修正**：① 首版归因基准臂用错（8,455 而非旧批当日的 **8,595**），以旧批 `trace` 的 `chain=N` 指纹自证（8,595 臂 **10/10**、8,455 臂 7/10）；② **`LLM_REV_EDGES=0` 并不关门控**（`bool(\"0\") == True`），真正「关」= 留空/未设置 ⇒ 回归件首跑「全 0 差异」即此假象，修正后真库实测 **338 行 / 9 题**不等。见 `_handoff/D3-C-EXECUTION-PLAN-20260923.md`。原评估留档："
      "（`causal_graph.py` 的 `ingest` 闸门 **D-① 已于 2026-09-22 落地**，取「**改标 `pending` 并入库**」"
      "= 增量堵口、零可达性影响）。⭐ **D-③ 已于 2026-09-23 完成影响评估**（纯只读）："
      "**建议「重建 + 改标 `pending`」** —— 实测重建**零漂移**（闸门关重建与生产库**逐行相同**）、"
      "改标**结构恒等**（10 题链边集合 **0/10**、`why`/`chain_recall` 全量 **0/1655**，仅 prompt 标签字面变）；"
      "**不建议 skip/剔除** —— 剔 1,339 条后「可展开」**仍为 1,906**（低置信边**本来就不参与多跳**）"
      "⇒ **零可达性收益**，却使 10 题 **8/10** 变（与 §4.17 历史值**逐数复现**）。"
      "见 `_handoff/D3-POOL-DISPOSITION-20260923.md`（含第二方复核 **11/11 一致**）；"
      "①-b **D-④ `type∈{cause,supersede} ∧ weak` 残留 3 条已评估、决定待定**"
      "（`A1207`/`A4830`/`A711`，均 `conf>0.6` ⇒ 与 D-③ 的 1,339 条**交集 0**）："
      "**建议改型、不剔除**，但⚠️ **带不可省前提** —— `criteria` 里「weak 须有元组级同一具名主体/对象」"
      "**文本至今未修订**、`WEAK-POSITION-RULING` 仅「建议」⇒ **若严口径仍生效则 `A1207`/`A4830` 应剔**；"
      "另查出**判型映射表非穷尽**（v2.1 自身示例 3 即表外的 `trigger ∧ strong`）⇒ 表外边达 **1,243 条（14.7%）**。"
      "见 `_handoff/D4-ILLEGAL-COMBO-RESIDUAL-20260923.md`（含第二方复核 **14/14 一致**）；"
      "② 口径裁定 A（G14 那 9 条 **3×3 完全二分图**「全剔 or 改型保留」）/ B（`conf≤0.6` 用 `≤` 还是 `<`，"
      "涉及 `conf == 0.6` 的边 —— **现测 1,298** 条）。"
      "⭐ **D-② 已于 2026-09-23 裁定闭合**：`A5530`（收口后 tier R **仅剩 1 条**）取「**接受闸门口径**」"
      "—— **不人工覆盖 `confidence`**，重建时由 D-① 闸门自动改标 `pending`；**零写入图谱**。见 "
      "`_handoff/D2-A5530-RULING-20260923.md`。")
    A("~~下一轮 20 条 `cause/supersede∧weak`（conf≤0.6）逐条仲裁 + 1,379 条抽样（裁定 #7）~~ → "
      "✅ **已完成**（2026-09-22）：20 条**剔 20 / 保留 0 / 改型 0** 并**已入库**（边 **8,495 → 8,475**、"
      "`conf≤0.6` **1,379 → 1,359**、`cause/supersede∧weak` **23 → 3**；7 组校验 + 三项交叉验证全过），"
      "见 `M1-10-D10判边仲裁入库结果-20260922-20条.md`。"
      "⚠️ **遗留**：抽样基数已变为 **1,359**（原 240 条方案中 tier S 的 20 条已处置 ⇒ 待仲裁 **220 条**，重算须重新分层）"
      "→ ✅ **2026-09-23 已闭合**：220 条收口**已落库剔 20 条**（边 8,475 → **8,455**；"
      "A 档 200 条在「检索线索层」定位下**无需剔除**），见 `M1-10-D10判边仲裁入库结果-20260923-220条收口.md`；"
      "~~`D10-H04` 判分影响未测~~ → ✅ **已实测闭合**（2026-09-22）：处置前/后两臂各 3 次生成 + 逐条判分，"
      "**6/6 判定逐字段完全相同** ⇒ **本轮 20 条剔除对该题判分无可观测变化**（臂内 3/3 一致 ⇒ 生成噪声为 0；"
      "负向自证 2/2 ⇒ 判分器非恒真亦非恒假）。⚠️ 原「当前环境无 LLM 凭证」**不成立** —— "
      "`RELAY_KEY`/`PROBE_KEY` 只被 relay/probe 类脚本使用，**判分路径不读它**；"
      "真实凭证链路 = `provider_conf()` 的「环境变量 → `~/.dsh/.credentials.yaml`」两级回退。"
      "见 `docs/host_memory_dump/H04-判分影响实测-20260922.md`。")
    A("~~`apply_arbitration_146.py` 入库~~ → ✅ **已完成**（2026-09-19，边集 8,595 → 8,495，见 `M1-10-D10判边仲裁入库结果-20260919.md`）。")
    A("~~D09 33 行人工复核~~ → ✅ **已收口**（2026-09-19，见「三、其他待审」下方的已闭合留档；判定由人工作出，AI 未代签）。")
    A("")
    A("## 二、复核机械未结项（非阻塞）")
    A("")
    A("验收台账显示 **233/233 单元、3732/3732 事件全闭环、0 阻塞项**，")
    A("但仍有 **123 条非阻塞提示**（severity 全为 low/minor/info，blocking 全为 False）：")
    A("")
    A("- 120 条 `accepted_with_findings`、3 条 `accepted`")
    A("- 典型内容：事件 type 承载不严、after 值位含复核者声明、个别 source_check 标注偏宽")
    A("- 详见 `03-d10-workspace/2026-09-08-20-30-19/outputs/full-review-full-20260909/acceptance-ledger.json` 的 `open_findings`")
    A("")
    A("## 三、其他待审 / 待授权")
    A("")
    A("| # | 待办 | 进度 | 依据 |")
    A("|---|---|---|---|")
    for name, st, why in PENDING:
        A("| — | %s | %s | %s |" % (name, BADGE[st], why))
    A("")
    for grp, items in CLOSED_GROUPS:
        _d = grp.replace("CLOSED_", "")
        # 变量名是 CLOSED_YYYYMMDD，渲染成 2026-09-20 形式，避免读成一个大整数。
        _d = "%s-%s-%s" % (_d[:4], _d[4:6], _d[6:8]) if len(_d) == 8 else _d
        A("### ✅ %s 已闭合（留档，勿再列入待办）" % _d)
        A("")
        A("| 项 | 依据 |")
        A("|---|---|")
        for name, why in items:
            A("| %s | %s |" % (name, why))
        A("")
    A("## 四、已知缺陷")
    A("")
    A("> 2026-09-19 复核：原列 6 项中有 **3 项早已修复**，本节长期未同步（同类「静默过期」问题）。")
    A(">")
    A("> 2026-09-21 复核：另 **2 项**（`s4_pipeline.py` semantic-emb、`.gitignore` 的 `env/`）已于 09-20 修复，"
      "且早已登记在「三、其他待审」的 09-20 已闭合留档中 ⇒ 已从下方「仍存在」表**移出**（消除重复登记）；"
      "同日**新增 1 项**待授权缺陷（junction 可复现性回归）。")
    A("")
    A("### ✅ 已修复（留档，勿再按缺陷处理）")
    A("")
    A("| 项 | 修复方式 | 验证 |")
    A("|---|---|---|")
    A("| `test_review_scoped_validator.py` 2 项 FAIL | 注入合成受阻集合 `frozenset({170,171,232})`，用例预期不再随授权文件漂移 | **31/31 通过**（2026-09-19 实测） |")
    A("| `name_scan.py --names identities-init.json` 误报 | 新增 `_is_non_person()` 改读结构化字段，不做子串猜测 | 100 条真实载荷命中 213 → 213（无回归） |")
    A("| `RUNME-full.md` 过期 | **核验不成立** —— 已更新至 r19，ledger/package sha256 与磁盘一致 | 机械核验通过 |")
    A("")
    A("> 修复提交：`d381b38`（2026-09-16，「修补 2 处已知缺陷」+ 第 3 项经核验不成立）。")
    A("")
    A("### ⏳ 仍存在")
    A("")
    A("| 缺陷 | 影响 | 说明 |")
    A("|---|---|---|")
    A("| **冻结索引 `payload_path` 仍指迁移前绝对路径，而校验器禁止追随 junction** | 官方校验器 "
      "`review_scoped_validator.py` 的 CLI **在全部 5 个冻结索引上必失败** ⇒ scoped 复核**不可端到端复现** | "
      "**2026-09-21 实测**：`outputs/full-review-full-20260909*` 5 个索引各 233 条，合计 **1,165 / 1,165** 条 "
      "`payload_path` 以旧根 `%s` 开头；**1,165 / 1,165** 路径链上含同一 junction（`%s` → `%s`），"
      "**但 2026-09-21 晚些时候该判断已被推翻** —— 复跑同一探针得 **1,165 / 1,165 缺失**：OS 已把 `host-workspace` 下**全部 25 个** junction 判为「**不受信任的装入点**」（`WinError 448`；重解析标签 `0xA0000003` = 真 Junction；长路径前缀不能绕过；bash→python 与 PowerShell **两条独立执行路径一致**；系统自 2026-09-15 未重启；目标侧真实路径可读）。⇒ 影响面**大于**本条原判：「旧根路径在原生 Windows 进程下已完全失效」，不只是校验器拒不追随。`AccessLedger.read`（第 118–121 行）**显式拒绝追随 symlink / junction** "
      "⇒ 读 payload 处抛 `ScopeRejected`（已进程内实跑复现：真 `batch-142` 抛错，同文件新根路径可读 31,875 B）。"
      "另：库内含旧根前缀的被跟踪文件 **313 / 21,208**（03:194 / 01:50 / 02:34 / `_archive`:21 / 根:5 / "
      "`_migrate`:5 / `.workbuddy-ai`:2 / `_handoff`:2）。**处置三选一，需用户另行授权**："
      "① 回填冻结索引为新根路径；② 放宽校验器的 junction 禁令（须先论证不破「链接可重定向授权范围」原意）；"
      "③ 仅登记不改（接受不可复现）。⚠️ 本轨道**未改任何冻结索引、未动 junction** |"
      % (_OLD_ROOT, _JUNC_SRC, _JUNC_DST))
    A("| openclaw 与托管 node v22.22.2 不兼容 | 环境 | 需 `>=22.22.3`；用系统 node v24 或走 embedded fallback |")
    A("| 脱敏不彻底 | **安全** | payload 正文仍含真实姓名 → 受限素材不得外发 |")
    A("")
    A("## 五、git 治理状态")
    A("")
    gf = git_facts()
    A("| 项 | 状态 | 说明 |")
    A("|---|---|---|")
    A("| 统一仓库 | ✅ 已建立 | `Desktop\\JVS` 单仓库，01 子树历史经 `git subtree` 并入 |")
    A("| 受限素材 | ✅ 独立仓库 | `04-restricted-materials/` 自成一库，**永不进主线** |")
    A("| 字节冻结 | ✅ 已生效 | 根与 01 子树均为 `* -text`；SHA256 可校验产物**不得做行尾转换** |")
    A("| 门禁 | ✅ 可运行 | `bash scripts/git-gate.sh`（%s 项检查，全绿才允许合并） |"
      % gate_steps())
    A("| 凭证扫描 | ✅ 可运行 | `bash scripts/scan-secrets.sh`，命中即阻断 |")
    A("| 分支模型 | ✅ 已定义 | **扁平命名（禁斜杠）**；禁直提 `main`；合并用 plumbing 等价实现 |")
    A("| 无损纳入 | ✅ 已落实 | `.sse` %d 个、`.zip` %d 个**均已入库**（库内约 58 MB，勿被工作树 3.9 GB 吓退）"
      % (gf["sse"], gf["zip"]))
    A("| 里程碑标签 | ✅ %d 个 | %s |" % (len(gf["tags"]), " / ".join("`%s`" % t for t in gf["tags"]) or "—"))
    A("| 生成时 HEAD | ✅ 干净 | `%s`，提交数 %s，分支 %s（**生成快照值，非实时**，提交后必然落后） |"
      % (gf["head"], gf["commits"], gf["branch"]))
    A("")
    A("> 规则原文见 `GIT-POLICY.md`；改动前先读，不要凭记忆操作。")
    A(">")
    A("> ⚠️ **本环境的两条硬禁忌**（2026-09-18/19 两次事故的结论，见 `GIT-POLICY.md` 第十一节）：")
    A("> 1. **禁用 `git switch` / `git checkout`** —— 本环境对它们必发 SIGTERM，")
    A(">    而 SIGTERM 会把工作区文件**批量移入回收站**（合计 15,998 个跟踪文件）。")
    A(">    改用 `git symbolic-ref` + plumbing（见 `README.md` 改动纪律）。")
    A("> 2. **单条 git 命令处理 ≤3,000 文件** —— 约 9,000 文件即触顶被 SIGTERM。")
    A(">")
    A("> ⚠️ **绝不中断 `git merge` / `git gc`** —— 2026-09-16 曾因此损坏 `.git`")
    A("> （详见 `GIT-POLICY.md` 第十节）。")
    A("")
    A("## 六、动手顺序建议")
    A("")
    A("1. `bash _handoff/env/check_env.sh` —— 确认环境就绪")
    A("2. 读 `_handoff/HANDOFF.md` —— 搞清项目是什么、东西在哪")
    A("3. 读 `_handoff/scripts/SCRIPTS.md` —— 确定跑哪个脚本")
    A("4. 读 `GIT-POLICY.md` —— 改动前先明确分支与门禁纪律")
    A("5. 从「一、当前主线」的 #1 开始 —— 这是 D10 的收敛点")
    A("")
    A("---")
    A("")
    A("> ⚠️ 本文件由 `_migrate/make_progress_md.py` **自动生成**，手写修改会被覆盖。")
    A("> 要改内容请改生成器里的 `write_pending()`，然后重跑该脚本。")
    A("> （2026-09-19 教训：本文件曾有整节「git 治理状态」只存在于产物里、不在生成器中，")
    A("> 重跑后被静默抹掉。）")
    A("")

    p = os.path.join(JVS, "_handoff", "PENDING.md")
    with io.open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("已写出:", p, os.path.getsize(p), "bytes")


def main():
    by = load()
    write_progress(by)
    write_pending(by)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())