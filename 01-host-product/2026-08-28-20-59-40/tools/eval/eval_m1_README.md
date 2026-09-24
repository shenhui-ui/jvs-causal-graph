# eval_m1 —— M1 判分器接入草案使用说明

本文中的 `eval_m1.py` 是离线规则评分器；真实模型三要素判分使用 `tools/causal/llm_judge.py`，其断点、空响应和服务错误口径见 `tools/causal/README-run.md`。两类结果必须分文件、分口径保存，不能互相覆盖 `availability` 或 `causal_accuracy`。

`eval_m1.py` 是 M1-14(判分器接入)的**草案**。它把既有的 `memory_eval.py`(位于
`tools/eval/memory_eval.py`)与 M1 立项书(v0.6 = M1 立项基线)要求的三个 M1 级指标对齐:
**系统可用率 ≥90%**、**因果准确率(主验收)≥60%/目标 70%**、**端到端合格率 ≥60%/目标 70%**、
外加**证据完整率**(三项原始数据之一,随附报告)。

它是**单文件、纯标准库**实现(仅用 `argparse / json / math / os / random / re / sys / datetime`),
**不调用任何 LLM、不联网、无第三方依赖**。真正的「因果三要素」语义判定走 `llm_check` 接口,
**默认实现为规则启发式(关键词/子串/结构判定),并明确标注「接入真实 LLM 判分时替换」**。

> **注意**:`python` 在部分 Windows 环境是 Microsoft Store 占位符。用真实解释器,例如
> `C:\Users\<user>\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe`。下方以 `python` 代指。

---

## 0. 一句话总览

```text
memory_eval.py convert   —— 把 C01b 因果题集 Markdown 解析成黄金 JSONL(已有工具,复用)
eval_m1.py score        —— 对系统输出 answers.jsonl 判分,输出 M1 指标 + 逐条明细
eval_m1.py report-md    —— 把 scores.json 渲染为 Markdown 报告(三项原始数据+对照区间)
eval_m1.py finalize     —— 人工仲裁回填(conflict_list + final → 重算 scores)
```

> M1 判断分母(真实题集 ≥30 题)用 `memory_eval.py convert --schema causal` 先产出黄金
> JSONL,再用 `--gold` 喂给本工具。本工具**不重复实现 Markdown→JSONL 解析**。

---

## 1. 运行方式

### 1.1 全局

```bash
python eval_m1.py --help              # 顶层帮助
python eval_m1.py --version           # 版本
python eval_m1.py score --help        # 各子命令独立帮助
python eval_m1.py report-md --help
python eval_m1.py finalize --help
```

### 1.2 判分(score)

```bash
# 基础:answers.jsonl 自带黄金三要素(内嵌),直接判分
python eval_m1.py score answers.jsonl --out scores.json

# 用 memory_eval convert 的黄金 JSONL 按 qid 补齐三要素
python eval_m1.py score answers.jsonl --gold gold_causal.jsonl --out scores.json

# 固定 seed + 同题 2 次判分(不一致写 conflict_list.jsonl；两轮结果与明细同源)
python eval_m1.py score answers.jsonl --gold gold_causal.jsonl \
    --out scores.json --seed 42 --double-check --conflict-out conflict_list.jsonl

# 可选参数
#   --timeout-ms 30000            逐题可用性耗时阈值(默认 30000ms，不是实测 P95)
#   --timeout-ms 180000           专家模式独立观察口径，必须显式记录，不覆盖 30 秒基线
#   --match-threshold 0.45        三要素内容覆盖判分阈值(默认 0.45)
#   --require-causal-structure    format_ok 额外要求答案含因果连接词
```

`--seed` 固定判分随机性;`--double-check` 对同题判 2 次,若两次结果不一致,把该题写入
`conflict_list.jsonl` 供人工仲裁,并输出**判分器一致率**(对应 M1 立项书 W1 检查点 ≥90%)。双判分与单判分使用同一 `--require-causal-structure` 规则。

### 1.3 出报告(report-md)

```bash
python eval_m1.py report-md scores.json --out report.md
python eval_m1.py report-md scores.json                      # 打印 stdout

# 可自定义硬/目标线
python eval_m1.py report-md scores.json --out report.md \
    --baseline 0.60 --target 0.70 --availability-baseline 0.90
```

报告含:**系统可用率**、**因果准确率(三要素逐项原始数据)**、**证据完整率**、**格式合规率**、
**端到端合格率**,以及每一项与**主验收硬线 60% / 目标 70%** 的对照与 95% CI。

### 1.4 人工仲裁回填(finalize)

```bash
# conflict_list.jsonl(双判分不一致清单)→ 人工判 → final.jsonl → 回填重算
python eval_m1.py finalize scores.json conflict_list.jsonl final.jsonl --out scores_final.json
```

---

## 2. 字段约定

### 2.1 输入 answers.jsonl(系统输出)

每条记录一个 dict(JSON Lines,坏行不中断、只告警):

| 字段 | 类型 | 说明 |
|---|---|---|
| `qid` | str | 题目唯一编号(必填,如 `R-C01`) |
| `answer_text` | str | 系统答案文本(必填;因果题应为证据链式回答) |
| `evidence_chain` | list[str] | 证据链;判分只看它是否含 `[src:` 引用 |
| `latency_ms` | number | 系统端到端耗时(毫秒);缺失视为不超时 |
| `pipeline_ok` | bool | 管线是否成功(必填) |
| `trace` | str(可选) | 系统 trace,仅透传/诊断,不参与判分 |

### 2.2 黄金三要素(cause / effect / evidence_hint)

M1 判分器把 C01b 因果题的「标准答案」(`memory_eval` 里的 `answer`)拆成**三要素**:

| 要素 | 含义(对应标准答案) | 赋值建议 |
|---|---|---|
| `cause` | 触发(为什么改,X) | 「因为 X 触发…」里的 X |
| `evidence_hint` | 决策者/证据(由谁提出,Y) | 「由 Y 提出(决策者)」里的 Y |
| `effect` | 目的(改了为了什么,Z) | 「目的是 Z」里的 Z |

黄金三要素读取优先级:**记录内嵌 > `--gold`**:
1. `answers.jsonl` 每条记录顶层直接带 `cause / effect / evidence_hint`
   (或一个 `gold` dict:`{cause, effect, evidence_hint, question, answer, schema}`);
2. `--gold <jsonl>`(`memory_eval.py convert --schema causal` 的产物)按 `qid` 补齐。

黄金三要素缺任一 → 该条**不参与因果准确率分母**(报为 `n_ungraded`)。

### 2.3 指标口径

- **规则可用率** `available_rate` = 可用题数 ÷ 总题数。**可用** = `pipeline_ok` 为真 且
  `answer_text` 非空 且 未超时(`latency_ms <= --timeout-ms`)。硬线 ≥90%。默认 30000ms 是保守基线阈值，不是当前专家模式实测 P95。
- **专家模式口径**：复核时显式指定事前约定的阈值，例如 `--timeout-ms 180000`，在报告中保留 `timeout_ms`、分子、分母。若事后对同批答案比较不同阈值，只能称为敏感性观察，不回写默认 30 秒基线。不要把规则可用率与 `llm_judge.py` 的语义 `availability` 混写。
- **三类可用性必须分开**：生成可用率（答案管线成功且正文非空）、判分成功率（`judge_ok=true` 的 gold 题数 / gold 总数）、规则超时可用率（本工具按指定 timeout 的结果）。
- **因果准确率** `causal_accuracy` = 三要素**全对**的题数 ÷ 可判分题数。硬线(主验收)≥60%,目标 70%。
  单要素判定走 `llm_check`(默认启发式)。使用 `--gold` 时以 gold 的 qid 集合作为验收全集，漏题按不可用/失败计入分母；重复或未知 qid 会拒绝评分。
- **证据完整率** `evidence_completeness`(单条)：含 `[src:` 或 `[src：` 的非空字符串条数 / 非空字符串总数；无有效条目时为 0。比例为 1 / 介于 0 与 1 / 0 时分别记 full / partial / none，整批取均值。它衡量规范引用比例，不证明引用事件存在或因果链正确；证据编号覆盖和语义 `evidence_hint` 须独立展示。
- **格式合规** `format_ok`:`answer_text` 为非空字符串;(可选 `--require-causal-structure`)再要求含因果连接词。
- **合格** `qualified` = 可用 且 三要素全对 且 `evidence_score>=1.0` 且 `format_ok`。
- **端到端合格率** = 合格题数 ÷ **可用题数**(可用基础上);同时给「全量分母」保守口径。

> 合格率公式(近似)= 因果准确率 × 证据完整率;格式项另行计,不纳入公式(见 M1 立项书 §2.4)。

---

## 3. 与 memory_eval.py 的关系

| | `memory_eval.py` | `eval_m1.py` |
|---|---|---|
| 定位 | 解析 + 规则化/结构化判分 | M1 级指标判分器接入草案 |
| Markdown → JSONL | **有**(convert) | **无**(复用,用 `--gold` 喂) |
| schema | qa / causal / error | causal(以 M1 因果题集为准;三要素) |
| 判分 | 命中率 +「当时/后来」结构 + 反事实人工清单 | 三要素 + 证据完整 + 格式 + 合格/可用 |
| M1 指标 | ❌ | ✅ availability / causal_accuracy / evidence_completeness / qualified |

一句话:**复用 memory_eval 的 schema(schema `causal`)与解析产物(黄金 JSONL),新增 M1 指标**。
工具解耦——用 `memory_eval.py convert --schema causal --out gold_causal.jsonl` 得到黄金,
再用 `--gold gold_causal.jsonl` 交给 `eval_m1.py` 判分。

---

## 4. 人工仲裁流程(冲突清单 → 人工判 → 回填 final.jsonl)

> 对应 M1 立项书对口径:LLM 输出不稳定 → **固定 seed + 同题 2 次取一致,不一致人工仲裁**。

```text
① 判分         python eval_m1.py score answers.jsonl --gold gold.jsonl \
                 --seed 42 --double-check --conflict-out conflict_list.jsonl --out scores.json
② 查看冲突     report-md scores.json 里看「判分器一致性」;或直接看 conflict_list.jsonl
                 每行含:qid / element / pass_a / pass_b / gold_text(供人工对照)
③ 人工判定     对每条冲突,人工在 final.jsonl 里写最终结论(示例见 §4.1)
④ 回填重算     python eval_m1.py finalize scores.json conflict_list.jsonl final.jsonl \
                 --out scores_final.json
⑤ 复跑报告     python eval_m1.py report-md scores_final.json --out report_final.md
```

### 4.1 final.jsonl 格式

每条一个 dict,按 `qid` 覆盖判定结果(仅能写本工具判分涉及的字段):

```json
{"qid":"R-C02","cause_correct":true,"effect_correct":false,
 "evidence_hint_correct":true,"evidence_score":0.5,"format_ok":true,"note":"目的缺失,人工判差"}
```

字段说明:
- `cause_correct / effect_correct / evidence_hint_correct` — 三要素人工判定(必须是真正 JSON bool,可只写部分,没写的沿用自动判定);
- `evidence_score` — 证据完整率人工分(0.0 / 0.5 / 1.0);
- `format_ok` — 格式合规人工判定(bool)。

`finalize` 会把这些覆盖应用到 `scores.json` 的逐条明细并**重算聚合指标**；只允许覆盖
`conflict_list.jsonl` 中实际冲突的 qid，布尔字段必须是真正 JSON bool。写出 `scores_final.json`
(写回后再用 `report-md` 出修复后的报告)。

---

## 5. 已知限制

1. **判分通道为规则启发式**(`rule_heuristic`)。三要素判定靠关键词/子串/结构 + 内容覆盖阈值,
   对答案表述差异较敏感,非语义理解。正式结题建议接入真实 LLM 判分(`llm_check` 替换后)
   再**人工抽检约 20%**。
2. **证据完整率**依赖 `[src:` 这个引用标记。若你的证据链采用其他来源标记(如 `[来源]`、`「出处」`),
   需同步扩展 `evidence_score` 里的判定,或将证据链预格式化为 `[src: …]`。
3. **可用性超时**用 `--timeout-ms`。默认 30000ms 仅作保守基线；专家模式建议显式传入 `180000`，并把该阈值写入报告。此阈值只影响离线规则评分，不改变模型请求 timeout。
4. **真实判分错误**不应改写为语义反例：`llm_judge.py` 的 `judge_ok=false` 代表服务/响应失败，`judge_ok=true, available=false` 才代表模型完成语义判定且答案不可用；两者都保留在 gold 固定分母中。
5. 主验收按**题数阈值**判定(18/30 硬、21/30 目标);本工具同时给 95% CI 便于对照
   (n=30 时 CI 半宽约 ±17.5pp,统计上属不可区分区,故以题数判定为准,见 §2.5)。

---

## 6. 交付清单

```text
eval_m1.py           M1 判分器接入草案(单文件,纯标准库)
eval_m1_README.md    本说明
```

常用命令(可在工作区直接运行):

```bash
# 1) 用已有 memory_eval 把 C01b 因果题集转成黄金
python memory_eval.py convert "C01b_因果扩展集.md" --schema causal --out gold_causal.jsonl
# 2) 判分(含双判分 + 冲突清单)
python eval_m1.py score answers.jsonl --gold gold_causal.jsonl \
    --seed 42 --double-check --out scores.json --conflict-out conflict_list.jsonl
# 3) 出报告
python eval_m1.py report-md scores.json --out report.md
```

---

## 附:判分口径说明(2026-08-29 评审修订,必须知悉)

### 1. 规则启发式的匹配宽松度(重要)
- 默认 `llm_check`(规则通道)使用**归一化子串 + 关键词重叠 + 2-gram 相似度阈值(`--match-threshold 0.45`)**的**宽松匹配**;
- **重校准对照**(30 条冒烟集):严格子串复算 = 因果 20/27(74.1%)、合格 12/27(44.4%);宽松通道 = 22/27(81.5%)、13/27(48.1%);**差异 2 条**(Q008「保留新环境 10% 观察」↔gold「保留 10% 观察」、Q014「需与某人确认」↔gold「待与某人确认」)均为**语义等价**,判对合理;
- ⚠️ **风险与对策**:同一宽松度可能放过「关键词重叠但语义错」的答案(本轮 5 条故意答错全部被拦,验证过一次,但样本小)。对策:**人工抽检 20% + 关键判定(验收题集)用 `--double-check` 同题 2 次 + 接入真实 LLM 判分通道(替换 `judge_channel=llm`)**;
- 结论:宽松通道用于**冒烟与回归的自洽验证**;**正式 Gate 3 判分建议启用 LLM 通道 + 人工仲裁 20%**。

### 2. 证据完整率(v0.7 起)= 有效引用比例
- ratio = 含 `[src:` 条数 / 非空总条数;grade: full(ratio=1.0)/ partial(0<ratio<1)/ none(0);
- **废除**旧规则「非空但无 [src: → 0.5」(给不可验证引用发半张卡);新规则下 Q029(1 src + 1 无源)= 0.5、Q030(0 src)= 0.0;
- **线索支持性条款(2026-08-30 抽检修正)**:非 `[src:]` 线索**须支持结论**才计入证据;与结论相抵/无关的线索计「无」(如 Q030「另有摆拍嫌疑」与结论「无大问题」矛盾 → none)。建议下一步将「支持性」判入 llm_check(规则版暂只做「矛盾词检测」粗筛);
- 严格口径下 30 条冒烟集 evidence mean ≈ 0.58(full 17 × 1.0 + partial 1 × 0.5 + none 12 × 0)/30;qualified 从 13/27 降至 **12/27(44.4%)**——**真实管线优先修复项**:Q013–Q020(30% 可用条目)零证据引用,是 evidence 的主要拖累。

### 3. 冒烟集合成值声明(勿用于统计)
- `smoke-answers.jsonl` 的 latency 为**合成值**(800/850/900/950/12000/99999/500),question 为合成句;只用于判分逻辑验证,**不得用于延迟统计或真实质量推断**;
- Q027 超时分支以 `pipeline_ok=False` 模拟(真实超时不产出完整答案;latency 仅标记),Q030 为对抗样例(证据与结论相矛盾);
- 其余合成注记:gold 人名含习惯性截断与别名(「同一人可能有别名」),接真实语料前统一为规范代号;各批材料中出现的「Q015」与真实题集审定里的「Q015」**并非同一题**(冒烟 Q015=摆拍判定、题集 Q015=真实性欠缺标注修正),勿混。
