---
name: jvs-restricted-review-backfill
description: 把 JVS 受限素材（04-restricted-materials/）的人工复核结论回填进台账与候选文件，并按既有版本标准做重审/口径统一。当需要「D09 复核回填」「受限候选人工复核落盘」「review.json 回填」「human_review_state 改 completed」「按 v3 标准重审」「跨版本口径统一」时使用。含先例对齐、备份、干跑零写入、自证式校验、幂等校验、历史快照不改、版本差异如实登记。
agent_created: true
---

# JVS 受限素材复核回填 / 重审

## 何时用

- 人工（用户）已对受限候选作出逐行判定，需把结论落进台账与候选文件
- 需**按另一个版本（如 v3）的标准重审**当前版本，做**跨版本口径统一**
- 排查「台账 `human_review_state` 一直是 `pending`」

## ⚠️ 三条不可越界

1. **AI 不代替人工签字、不作独立语义裁决** —— 判定必须来自**用户本人的逐项选择**
2. **授权分层** —— 用户授权「呈现候选正文」**≠** 授权呈现**源原文**
   （`incoming_real/` 比候选更敏感，呈现时须遮蔽真名）
3. **授权前不改状态** —— 回填属「授权后」动作；「授权回填」≠「授权变更验收状态」

## 第 0 步：先找既有先例，**不要自行发明取值** ⭐

这是本流程最容易做错的一步。JVS 的 D09 有 v1/v2/v3 三个版本，**先查哪个已完成复核**：

```bash
PY="C:/Users/<user>/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe"
"$PY" -B -c "
import io,json,os
base=r'04-restricted-materials\restricted-review'
for v in sorted(os.listdir(base)):
    p=os.path.join(base,v,'review.json')
    if os.path.exists(p):
        d=json.load(io.open(p,encoding='utf-8'))
        print(v, d['human_review_state'], d['human_reviewer'], d['overall_status'])
"
```

2026-09-19 实测：

| 版本 | state | reviewer | overall_status | line_audit |
|---|---|---|---|---|
| v1 | `completed` | <user> | `human_reviewed_internal_use_only` | 33（**27 accepted / 6 dropped**，2026-09-19 重审后）|
| **v2** | **`pending`** | **None** | **`pending_human_review`** | 31（30 pending / 1 dropped）|
| v3 | `completed` | <user> | `human_reviewed_internal_use_only` | 28（27 accepted / 1 dropped）|

> **跨版本对照用 `fragments[].candidate_body_lines`，不要用 `line_audit` 条数** ——
> 两版的**记账惯例不同**：v3 把摘除行**排除在 `line_audit` 之外**（故 28 条），
> v1 按自身惯例**保留条目并标 `dropped_by_review`**（故 33 条）。
> 重审后 v1 的 `candidate_body_lines` = `[6, 7, 13, 0, 1]`（合计 **27**），与 v3 **逐片段一致**
> ⇒ **有效批准集相同**。这才是「口径统一」的判据。

**取值规范**（照抄 v3，勿自创）：

| 层级 | 通过 | 剔除 |
|---|---|---|
| `line_audit[].status` | `human_reviewed_accepted` | `dropped_by_review` |
| `line_audit[].human_review` | `accepted` | `dropped` |
| `fragments[].context_status` | `human_reviewed_sufficient_for_cited_scope` | `dropped_by_review` |
| `fragments[].human_review` | `accepted` | `dropped` |

顶层：`overall_status = human_reviewed_internal_use_only`、
`human_review_state = completed`、`human_reviewer = <user>`、
`human_reviewed_at = <ISO 时间>`、`external_release_approved = false`（保持）。

**v3 的两条做法同样照抄**：

1. **`scope.json` 不改** —— 它是「提议范围」的历史记录。
   `review.json` 的 `candidate_body_lines` 与 `scope.json` 的 `line_count`
   不一致是**设计如此**（v3 的 P03 即 18 vs 13）。
2. **被弃用条目保留在台账**（`dropped_note` 写「保留以维持审计轨迹」），正文不复制；
   候选文件里写「已弃用 + 依据」而非直接删段。

## 流程

### 1. 备份（写之前）

```bash
TS=$(date +%Y%m%d-%H%M%S)
mkdir -p "C:/Users/<user>/_staging/_d09_backfill_$TS"
cp 04-restricted-materials/restricted-review/<ver>/* \
   "C:/Users/<user>/_staging/_d09_backfill_$TS/"
```

并**登记改前哈希** —— 事后用它证明「未处置文件字节未变」。

### 2. 写回填脚本（三模式）

参考实现：`C:/Users/<user>/_staging/tools/d09_review_backfill.py`

- `--check`：干跑，**零写入**
- `--apply`：备份 → 写 → 自动校验
- preflight：断言 `schema_version`、`line_audit` 条数、剔除项在册、当前 state 合法

**关键实现要点**：

- JSON 必须 `json.dumps(d, ensure_ascii=False, indent=2)` —— 实测与原文件**逐字节一致**，
  这是「未处置部分字节不变」的前提
- 写文件用 `newline=""`（不转换换行；受限目录为纯 LF）
- 剔除项**写死在脚本里**（可审计），不靠运行时推断

### 3. 校验

脚本内自动：accepted/dropped 计数、顶层字段、候选文件已改、**未处置文件字节未变**
事后独立：重读确认 + 再跑 `--check` 应报 **0 变更**（幂等）

### 4. 干跑零写入必须**证明**

跑完 `--check` 后逐文件对比哈希，应全部 `SAME`。不要只说「干跑过了」。

### 5. 登记哈希变化

只登记**实际变化**的文件；未变的要显式写「未变」。

## ⚠️ 坑

### 坑 1：历史快照不得篡改

`02-m1-evaluation/outputs/m1-10-d09-pilot-<date>/execution.json` 与 `verification.json`
里的**哈希**与 `human_review_*` 字段，是**生成时**的快照。
回填后**不修改它们** —— 篡改历史会破坏审计轨迹。

两者并存不矛盾：前者记「生成时状态」，复核记录记「复核后状态」。
**但必须在复核记录里写明这一点**，否则将来读 `verification.json` 会误判
「复核还没做」（典型静默过期）。

### 坑 2：版本间判定差异必须如实登记

实测：v1 复核**剔除** L0213 而 v3 **保留**；v1 **接受** L0231/L0238/L0240 而 v3 **摘除**。
根因是两轮**标准不同**（v1 按五项判据，v3 另加「context 行是否对举证有用」）。

⇒ 写进复核记录专节，列出「行 / v3 判定 / 本次判定 / 差异方向 / 根因」，
处置选项交复核者决定，**AI 不自行裁决、不静默掩盖**。

### 坑 3：受限文件在索引口径内、但**不在主线仓库**内

`classify.py` 会索引 `04-restricted-materials/` 下文件（记路径与尺寸），
所以回填后索引层会变（索引校验段 4 会报尺寸变化）。**这是正常的**。

⚠️ **更正（2026-09-19）**：`04-restricted-materials/` **并非「不在 git 内」** ——
它是**独立的本地 git 库**：自带 `.git`、`hooksPath=/dev/null`、**无远端**、
`.gitignore` 明写「仅本地，永不配置远端、永不外发」。
根仓库 `git ls-files "04-restricted-materials"` 为空 ⇒ **根仓库不跟踪它**。

⇒ **改动有版本库兜底**，但仍须另做工作区外备份（双保险）。改前先看它的 `git log`：

```bash
cd 04-restricted-materials && git log --oneline -5 && git status --short
```

提交时**必须显式核验受限文件未进主线**：

```bash
git diff --cached --name-only | grep -c "restricted-materials"   # 必须为 0
```

### 坑 4：授权范围要分清

回填完成后 `overall_status` 可改为 `human_reviewed_internal_use_only`，
但 **「D09 是否验收通过」是另一个决定**，需用户明确授权。
用户说「授权」时，若未明确覆盖验收状态，**保持原状态并写进待办**。

验收判定落档（2026-09-19 实例）新增 4 个留痕字段，**不改动任何判定与正文**：

```json
"acceptance_verdict": "通过（限内部使用）",
"acceptance_verdict_scope": "internal_use_only",
"acceptance_verdict_at": "<ISO 时间>",
"acceptance_verdict_basis": "<裁定依据 + 与对照版本一致性的说明>"
```

⚠️ **落档时点必须在「重审」之后**，使验收判定基于**最终批准集**；
且 `external_release_approved` **必须保持 `false`**（`source_identity_authenticated=false`）。

### 坑 5：子串匹配是校验重灾区 —— 必须用「自证式校验」⭐

**实测失效三形态**（2026-09-19，工具自报「全部通过」当时是**假阳性**）：

| 形态 | 代码 | 后果 |
|---|---|---|
| **子串假阳性** | 校验项 `"L0213 " in s2` | 说明文字里含 `L0213 ` ⇒ **恒真** |
| **守卫子串误判** | `if "L0213 " not in s0:` 用来判「正文行是否已存在」 | 被说明文字满足 ⇒ **跳过插入** ⇒ 正文 6 行 / 台账 7 行 |
| **字段名错配恒真** | `o.get("size")` 而字段实际是 `s` | `None == None` ⇒ 假阴性 |

**修法**：

1. 判「某行是否存在」一律按**行首**：`any(ln.startswith("L0213 ") for ln in s.splitlines())`
2. 加**自证式校验** —— **直接数磁盘上的事实**，不复用台账里的数字：

```python
def body_lines_by_fragment(path):
    """返回 {fragment_id: 正文实际行数}；正文行 = 以 `Ldddd ` 开头。"""
    cur, out = {}, {}
    for ln in rd(path).splitlines():
        m = re.match(r"^##\s+(P\d+)\s*$", ln)
        if m:
            cur = m.group(1); out.setdefault(cur, 0); continue
        if cur and re.match(r"^L\d{4} ", ln):
            out[cur] += 1
    return out
```

   然后逐片段断言 `磁盘行数 == 台账 candidate_body_lines`。**这条才是能真正拦住上面三种形态的判据。**

### 坑 6：期望值必须找「第二来源」对账，别自己跟自己对

重审前把结果算成 **`29 accepted / 4 dropped`**，正确是 **`27 / 6`** ——
`29/4` 对应的是「只摘 3 行」的旧方案（漏了后补的 241/242）。该误值一度写进台账。

**验算**：`31 +1(L0213 恢复) −5(摘除) = 27`；`2 −1 +5 = 6`。

**对账方式**：与**对照版本**逐片段比 `candidate_body_lines` ——
v3 为 `[6,7,13,0,1]`（合计 27），重审后 v1 也是 `[6,7,13,0,1]` ⇒ 一致。
**这个判据当场就能暴露 29 是错的。**

### 坑 7：「判定」与「处置」是两件事 —— 口径统一要同时对齐

v3 的 `omitted_lines = [231,238,240,241,242]`（**5 行**），
但差异登记原只写了 3 行，因为 241/242 被记为「**判定一致**」（两版都认定「非脱敏问题」）
—— 那**未反映处置不同**：v1 保留空行并**计入 accepted**，v3 **摘除**。

⇒ 跨版本对账时**必须同时比「判定」与「处置」**，否则会漏行。
给出选项时要把两种口径都摆出来（本例：B1 只统一判定 → 15 行；**B2 一并统一处置 → 13 行**）。

### 坑 8：重审的判定要「引自既有台账」，不是新裁

裁定「按 v3 标准重审」时，**全部判定逐条引自 `d09-pilot-v3/review.json`**，
含 `citation_qualifier` **逐字节照抄**（如 `context_only+direction_conflict_with_216`）；
工具只做**搬运与落盘**，不做语义判断。

⇒ 这是「**AI 不自行裁决**」在重审场景的落地方式：
若工具里出现任何 v3 台账里没有的判定，就是越界。

### 坑 9：错落盘要「从备份还原 → 修断言 → 重跑」，不要就地打补丁

本轮首次 `--apply` 落了两处错（期望值 29/4 + L0213 未插入）。
处置：**从备份整份还原到重审前状态**，修工具，再 `--apply` 一次 ——
让工具始终是**唯一事实源**，而不是在错误结果上手工修补。
三次备份全部留档（`_staging/_d09_realign_*`），并在提交信息里如实登记这次错误。

### 坑 10：**标准必须落规范** —— 两套标准并存会静默产生不同结论 ⭐

D09 的 v1 与 v3 各自都做了人工复核、都写进了自己的 `review.json`，
但**判据不同**：v1 按原交付摘要的五项判据逐行判定，v3 另有「context 行是否对举证有用」标准。
结果同一片段 `F02/P03` 一个 18 行、一个 13 行 —— **没有任何报错**，
直到两版对照才暴露。

⇒ **判据/口径必须写在「规范」里，不能只存在于某一版 `review.json` 或某一轮的口头结论里。**
本项目的复核规范 = `03-d10-workspace/2026-09-08-20-30-19/HANDOFF-full-review-20260910.md` **§6**
（§6.1 为 2026-09-19 新增的**规则 A** 复核声明不得进 `after` 值位 / **规则 B** context 行摘除标准）。
**做重审前先读 §6.1**，否则你会用第三套标准再判一遍。

> 同类信号：本轮另一处「改纪律未同步技能」（技能说结论落「§12 登记表备注列」，
> 而 §12 已改为独立表）—— **改了一条纪律必须全库搜旧表述**。

## 收尾清单（本技能专属项）

> 通用收尾序列见 `_handoff/DEFINITION-OF-DONE.md`（相对 JVS 仓库根）。

- [ ] **受限库内先提交**（`04-restricted-materials` 是独立本地库），提交信息如实登记任何错误落盘
- [ ] 复核记录文档补 §执行记录（按轮分节）/ §授权与签字 / §版本差异 / §待办 / §追加裁定 / §下游同步
- [ ] **全库检索旧断言**（一项裁定会同时让多份文档失效）：用 `git grep -lF '<旧表述>' HEAD -- .`
      逐条扫 —— 本项目靠这条才一次找齐 6 份文档；**别只改「我知道的那几份」**
- [ ] `_migrate/status_map.py`：`STATUS` 表 + `PENDING` 移除已闭合项 + `CLOSED_<日期>` 登记
- [ ] `_migrate/make_progress_md.py`：**产物里出现的文本必须改生成器**（改产物无效，会被覆盖）；
      并确认 `CLOSED_*` 列表**真被渲染**（否则收口项静默消失）
- [ ] `_handoff/HANDOFF.md`：从「未做」移入「本轮已闭合」
- [ ] `README.md` 未完成清单；其余报告类文档的待办表（按「历史快照不改写」原则只加收口标注）
- [ ] **核验提交不含受限文件**

## 相关实现（留档）

| 用途 | 路径 |
|---|---|
| 回填工具 | `C:\Users\<user>\_staging\tools\d09_review_backfill.py` |
| 重审工具（含三处缺陷复盘） | `C:\Users\<user>\_staging\tools\d09_review_realign.py` |
| 验收判定工具 | `C:\Users\<user>\_staging\tools\d09_acceptance_verdict.py` |
| 判定来源（重审对照） | `04-restricted-materials/restricted-review/d09-pilot-v3/review.json` |
| **复核规范（重审前必读 §6.1）** | `03-d10-workspace/2026-09-08-20-30-19/HANDOFF-full-review-20260910.md` §6 / §6.1 |
