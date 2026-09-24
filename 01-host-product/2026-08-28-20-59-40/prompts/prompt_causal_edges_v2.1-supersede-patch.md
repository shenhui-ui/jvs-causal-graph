# 判边提示词 v2.1 补丁(supersede/strong 判据扩展)

> 背景:`docs\M1-审计结论与决策A-20260831.md`(决策 A,2026-08-31)。v2 的 strong 判据=「明确因果句(因为…所以/由于/导致)」,导致 **supersede(方案替换)与 decision→change 衔接被压成 weak/evolve**(549 边仅 6 strong)。本补丁扩展 strong 判据,**不改变 weak 语义**,铁律(v2 §5)不变。

## 应用方式(二选一)

- **A(推荐)**:将下方「§strength 分级标准(替换 v2 同节)」直接并入 `prompt_causal_edges_v2.md` 生成 `v2.1` 全量文件;
- **B**:在 v2 payload 的「本批格式说明」末尾追加本补丁的「strong 判据扩展」一段(不改原文件)。

## §strength 分级标准(替换 v2 对应节)

- **strong** — 满足任一:
  1. 原 v2 判据:存在明确因果/触发/替换句,可读出「因为 X 所以 Y」「由于 X 导致 Y」「替代了 A」「把 A 改为 B」「由 X 改为 Y」「从 X 变成 Y」「换成/取消/替换」等因果或**变更标志句式**;
  2. **决策→变更衔接**:同一 subject/主线上的 `decision`(明确决定)与后续 `change`(该决定的落实/变更),时间先后、对象一致 → 判 `supersede`(若为方案/版本替换)或 `cause`(若决策句含理由)或 `trigger`,**strength=strong**;
  3. 变更事件(after)自带 before→after 差分且对象明确(如「由 5 秒改为 8 秒」)→ `supersede`,strength=strong。
- **weak** — 保持 v2:同主题连续推进但无上述句式/差分/决策衔接(补充、细化、报告、指派等),仅展示/线索、不参与多跳。
- 两者皆无 → 跳过。

## 判型映射补充(建议并入 type 节)

| 情形 | type | strength |
|---|---|---|
| 原文含「把A改为B/由X改为Y/从X变成Y/替换/换成/取消」 | supersede | strong |
| decision(定案) → 同对象 change(落实) | supersede / cause | strong |
| 变更含 before→after 差分且对象明确 | supersede | strong |
| 其余同主题推进 | evolve / trigger | weak |

## 验收(重判后)

- strong 边占比应从 6/549(≈1%)显著提升(目标 ≥10%);
- 0 条编造边(§2.3 统计口径,168 对抽检);
- supersede 类出现且集中于「方案/配置/参数变更」事件对。
