# R3 执行记录 —— P2-4 最小闭环 7 条落 r20（2026-10-20）

> **决议**：R3 = **B 最小闭环**（只做语义 8 条：P1 的 7 条 + `#43`；其余 23 条登记为已知限制）。
> **依据**：`_handoff/P2-4-69条无信号项实质分诊-20261018.md` §4 处置清单。
> **本次落 8 条**：`#02`/`#05`/`#17`/`#20`/`#36`/`#43`/`#63` 落 **r20**（8 批次）；
> `#04` 因**改变决策**（`reject→revise`）按红线 7 单列，经项目方 2026-10-20 对话框裁定后落 **r21**（见 §四）。

---

## 一、落了什么（8 批次 → `review-revision-r20/`）

| # | 批次 | 对象 | 改动 |
|---|---|---|---|
| `#02` | batch-102 | `G02-C03-0006`（revise） | `after` 删复核声明「；没有宣称已经寄出」（声明移入 `reason`） |
| `#02` | batch-103 | `G02-C03-0025`（revise） | `after` 删「；非原发言人现场确认」（移入 `reason`） |
| `#05` | batch-105 | `G02-C05-0013`（revise） | `after` 删「；未核实实际关闭状态」（移入 `reason`） |
| `#20` | batch-110 | `SC-G03-C01-074` | `fact` 末句「P07说明已与若鹏碰过正在处理」→「P07说明先与若鹏碰一下」（行299原文；原句越出本 SC 证据、且改变时态） |
| `#36` | batch-146 | `G08-C01-0027`（**keep→revise**） | `object`/`after` 补回限定词「**暂时**」（行267「后续这类型场景会暂时不新增了」） |
| `#17` | batch-112 | `SC-G05-C01-R1-057` | 其 `mapped` 的 `E022` 已 `reject`、事实无落点 ⇒ **补 1 条 addition**（引同一证据 165-166） |
| `#63` | batch-004 | `SC-G01-C02-027` | `cov 0→0.5`、`mapped=[]→[E021]`；**删去与该 SC 同文的重复 addition** |
| `#43` | batch-165 | `G10-C02-0022`（**keep→revise**） | `after`「查看…数据未上传原因」→「**核查**…统计不到的原因及上传情况…」（行214-216 未断言未上传） |
| `#04` | batch-105（**r21**） | `G02-C05-0010`（**reject→revise**） | `type change→report`（`report` 入枚举后原拒绝理由失效；`severity material→low`、`tags=[type]`；SC-013 保持 0.5 **不抬分**） |

**两条 keep→revise 的硬约束**（`review_scoped_validator.py:288-301`）：改事件字段必须把 `decision` 由 `keep` 改 `revise`，
并按冻结事件重建 `revised_event`（键序一致、只改目标字段）。

---

## 二、⭐ 官方校验器**跑不了**（被 R2 的 junction 限制挡住）

按 R3 要求应「重跑 `review_scoped_validator.py`」，实测**全部 7 个单元均 `incomplete_or_invalid`**：

```
batch-102: 无法完成所选文件验证: 禁止追随符号链接或 junction:
           C:\Users\<user>\host-workspace\outputs\m1-10-d10-event-extract-full-20260908
```

**根因**：批次里的 `payload` 字段仍指向**旧根**（`C:\Users\<user>\host-workspace\...`），该根下是 junction；
校验器**显式拒绝追随 junction**。这正是 **R2 = ③「仅登记、接受不可复现」**所接受的后果。

⇒ **不是本次改动的错**，是 R2 决议的直接代价。两条路：
1. **接受**：R3 的「官方校验」环节**永久不可执行**，改用下面的独立复核；
2. **回退 R2 到 ②**（放宽 junction 禁令）或 **①**（回填新路径）—— 需项目方另裁。

---

## 三、独立结构复核（替代手段，判据可拒绝）

脚本：`03-d10-workspace/2026-09-08-20-30-19/verify_r20_independent.py`
产物：`review-revision-r20/independent-verification-r20.json`

| 判据 | 结果 |
|---|---|
| 与源层逐键 diff：只允许「目标字段 + `reason`/`note` + `review_method`」变化 | ✅ 8/8 批通过 |
| 结构计数（decisions / source_checks / additions） | ✅ 全部符合预期（含 batch-112 additions 68→**69**、batch-004 additions 2→**1**） |
| **引文逐字核对**（quote vs payload 对应行） | ✅ **560 条**全部逐字一致 |
| 两条 keep→revise 的 `revised_event` 键序 + 差异面 | ✅ 键序与冻结事件一致；差异仅目标字段（`#36` 仅 object/after，`#43` 仅 after） |
| **负向自证**：故意改坏一条 quote | ✅ 判据报错（证明「能拒绝」） |

**总错误 0。**

⚠️ **边界**：独立复核只覆盖**结构/引用/差异面**，**不做语义判断**（与官方校验器同一分工）；
`semantic_review_performed = false` 的性质不变。

---

## 四、`#04` —— 已裁并落 r21（`reject→revise` / `type=report`）

| 项 | 内容 |
|---|---|
| 对象 | `G02-C05-0010`（E010）｜原文行 105–107（05-28 15:06）「Ego 电池和 Finger 目前不支持快充，只能用慢充，尤其 Finger 只能用 5v 输出供电」 |
| 原判 | `reject`，理由「没有前后配置变化；**七类事件中无一般技术说明类型**，不能以 change 登记」 |
| 复裁结论 | **改 `revise` + `type=report`** —— `report` 已于 2026-09-15 入枚举，原拒绝理由失效；事实为供电能力说明，无状态迁移 |
| 落点 | `review-revision-r21/G02-c05/batch-105.review.json`（在 r20 基础上叠加；`r21-fix-receipt.json`） |
| 字段 | `type change→report`、`severity material→low`、`tags=[type]`、`before=null`（沿用）、`after` 沿用冻结值 |
| 不抬分 | `SC-G02-C05-013` 的 `coverage` **保持 0.5**，不因改判提高 |
| 引文核对 | 该条证据 3 行与 payload **逐字一致（0 不一致）** |

> ⚠️ **过程说明**：本条本应由「独立上下文复核者」出意见，但子代理**429 限流**未跑成
> ⇒ 改为**把材料（原文/原判理由/报告建议/三种改法的影响）交项目方直接裁定**（红线 7：AI 不作独立语义裁决）。
> 项目方选择「改 revise + report」。

## 五、未做（登记）

- 其余 **23 条**处置项（P2 的 7 条 + P3 的 7 条 + 定点/批量中未选者）：按 R3=B 登记为已知限制，**本轮不动**；
- 官方 `review_scoped_validator` 与 `review_full_validator`：受 §二 限制，**本轮未跑通**；
- 未回填 `event-reviews.json` / `acceptance-ledger.json` 的汇总层（r20 只写新层，**旧层不触碰**）。
