# memory_eval —— 记忆层评测工具使用说明

`memory_eval.py` 是个人工作助手「记忆层」评测集(**C01 / C01b / C02**)的可运行评测工具。
它是**单文件、纯标准库**实现(仅用 `argparse / json / re / os / sys / abc / inspect / importlib.util`),
**不引入任何第三方依赖,不调用任何 LLM**。

本文档覆盖:命令示例、三种 schema 说明、指标定义(含公式)、如何接入真实召回/纠错管线、已知限制。

---

## 0. 一句话总览

四个子命令,一条流水线:

```text
convert  把评测集 Markdown 解析成 JSON Lines(黄金数据)
dryrun   基于黄金数据生成「预测模板」,交给人/管线填写
score    读取预测记录(或 --plugin 现场生成)计算指标
report   把 score 输出的 JSON 渲染成 Markdown 汇总报告
```

> **注意**:`python` 在部分 Windows 环境是 Microsoft Store 的占位符(`python.exe` 位于 `WindowsApps`)。
> 使用真实解释器,例如:`C:\Users\<user>\.workbuddy\binaries\python\versions\3.13.12\python.exe`。
> 下列示例用 `python` 代指任意可用解释器。

---

## 1. 用法与命令示例

### 1.1 全局帮助与版本

```bash
python memory_eval.py --help        # 顶层帮助
python memory_eval.py --version     # 版本号
python memory_eval.py convert --help     # 各子命令也有独立帮助
python memory_eval.py score --help
python memory_eval.py report --help
python memory_eval.py dryrun --help
```

### 1.2 转换 Markdown -> JSONL

```bash
# C01 跨时间线问答
python memory_eval.py convert docs/C01.md docs/C01b_因果扩展集.md \
    --schema qa --out gold_qa.jsonl

# C01b 因果扩展集(A 部分因果 / B 部分反事实)
python memory_eval.py convert docs/C01b_因果扩展集.md \
    --schema causal --out gold_causal.jsonl

# C02 错误记忆基准
python memory_eval.py convert docs/C02.md --schema error --out gold_error.jsonl
```

转换时无法解析的行会向**标准错误**打印警告并计入数量,**不会中断**。

### 1.3 生成空预测模板

```bash
python memory_eval.py dryrun --gold gold_qa.jsonl --schema qa --out pred_qa.jsonl
python memory_eval.py dryrun --gold gold_causal.jsonl --schema causal --out pred_causal.jsonl
python memory_eval.py dryrun --gold gold_error.jsonl --schema error --out pred_error.jsonl
```

模板中已带回 `question / type / difficulty / standard_answer` 等黄金字段,人工只需填写预测部分:
- `qa / causal`:`predicted_answer`(单个)或 `answers`(排序后的候选列表,得分用到 top-1/top-3)。
- `error`:`predicted_actions`(修正动作列表)与 `predicted_corrected_memory`(修正后的正确记忆)。
- 反事实(CF-*)模板还带 `human_marked_inference / human_refs_fact_layer / human_gives_confidence` 三个布尔占位,
  以及 `human_note`,供人工在判分时勾选。

### 1.4 计算指标

```bash
# 读取预测文件;预测记录里已带黄金字段(模板生成),可直接判分
python memory_eval.py score pred_qa.jsonl --schema qa --out scores_qa.json

# 预测记录缺少黄金字段时,用 --gold 按 id 补齐
python memory_eval.py score pred_causal.jsonl --schema causal \
    --gold gold_causal.jsonl --out scores_causal.json

# 若不传 --schema,会尝试从记录/gold 的 schema 字段自动推断
python memory_eval.py score pred_error.jsonl --gold gold_error.jsonl --out scores_error.json

# 命中相似度阈值默认 0.4,可按需调整
python memory_eval.py score pred_qa.jsonl --schema qa --gold gold_qa.jsonl \
    --match-threshold 0.35 --out scores_qa.json
```

### 1.5 输出汇总报告

```bash
# 打印到 stdout
python memory_eval.py report scores_qa.json

# 或写入 Markdown 文件
python memory_eval.py report scores_causal.json --out report_causal.md
```

### 1.6 通过插件接入真实召回/纠错管线

```bash
# qa/causal 用 Recaller;error 用 Fixer
python memory_eval.py score --plugin my_recaller.py --gold gold_qa.jsonl \
    --schema qa --out scores_plugin.json
python memory_eval.py score --plugin my_fixer.py --gold gold_error.jsonl \
    --schema error --out scores_plugin.json
```

插件文件会被动态加载;若其中定义了多个子类,可用 `--recaller-class` / `--fixer-class` 指定,
否则自动探测第一个「继承自 Recaller / Fixer」的类。

---

## 2. 三种 schema 说明

### 2.1 `qa`(C01 跨时间线问答,40 条)

qid 前缀 `F / C / T / R` 对应题型:事实 / 因果更新 / 时间衰减 / 纠错。字段:

| 字段 | 含义 |
|---|---|
| `id`(qid) | 唯一编号(必填) |
| `type` | 题型(缺省由 id 前缀推断) |
| `question` | 问题(必填) |
| `answer` | 标准答案(必填) |
| `source` | 源材料 |
| `source_ref` | 信息出处 |
| `difficulty` | 难度 低/中/高 |
| `distractors` | 迷惑项 |
| `note` | 备注 |
| `related` / `basis` / `confidence` | 供因果/反事实题复用 |

### 2.2 `causal`(C01b 因果扩展集,18 条)

- **A 部分(12 条)`R-C01…R-C12`**:因果「为什么改了」,标准答案为证据链 `因 X 触发 → 由 Y 决策 → 目的 Z`。
  计入**事实层自动判分**,同时做**证据链结构判定**。
- **B 部分(6 条)`CF-01…CF-06`**:反事实「如果当时没改会怎样」。答案**要求标注「推断,非事实层」**,
  且仅能基于事实层已知结果反推,置信度只允许 中/低。**不做自动判分**,输出「人工判分清单」。

字段除 qa 的全部外,还有 `part`(`A` / `B`)、`evidence`(证据句)、`basis`(依据)、`confidence`(置信度)。
`part` 由 `id` 前缀自动注入(`R-C…`→`A`,`CF-…`→`B`)。

### 2.3 `error`(C02 错误记忆基准,60 条)

`FT/ST/DP/AM/CT` 五类各 12 条。字段:

| 字段 | 含义 |
|---|---|
| `id` | 编号(必填) |
| `type` | 类型(由 id 前缀推断) |
| `input` | 原始对话输入(必填) |
| `wrong_mem` | 错误记忆(必填) |
| `expect_action` | 期望修正动作(必填,可组合) |
| `correct_mem` | 修正后的正确记忆(用于过纠率判定) |
| `difficulty` / `checkpoint` | 难度 / 检验点 |

期望/预测修正动作取自固定词表(可组合):`删除`、`降级为待确认`、`合并为一条`、`按时间更新`、`补全信息`。
`parse_actions` 会把字符串(`删除/降级为待确认` 或 `删除、合并为一条`)拆成规范化集合再比较。

---

## 3. 指标定义(含公式)

### 3.1 qa / causal-A:命中率与结构判定

- **Top-1 命中率** = 首候选命中的条目数 ÷ 有标准答案且给出候选的条目数。
  `top1_hit_rate`。命中判定为启发式(`texts_match`):归一化后相等、或一方包含另一方、或字符二元组
  Jaccard 相似度 ≥ `--match-threshold`(默认 0.4)。
- **Top-3 命中率** = 前 3 个候选中任一命中的条目数 ÷ 可判分条目数。`top3_hit_rate`。
- **按题型分拆** 与 **按难度分拆**:每一分组独立计算条目数、Top-1、Top-3 与 Top-1 率。
- **因果题结构判定** `structure_pass_rate`:对题型为「因果更新」(或 causal A 部分)的题目,
  检查预测首答案是否**同时包含「当时」与「后来」双时点**。这是规则化启发式,
  与证据链语义判分正交,**不作为命中率的一部分**。

### 3.2 反事实(causal-B):不自动判分,输出人工清单

对每条 CF-* 题目,`score` 生成一条「人工判分清单」,并给出**自动预检三要素**(供参考,最终由人工勾选):

| 要素 | 判定规则 |
|---|---|
| 是否标注推断 | 预测答案含「推断」或「非事实层」 |
| 是否引用事实层 | 含「事实层」、或出现 `C0X`(如 C05)、或含「依据」 |
| 是否给出置信度 | 含「置信度」或「可信度」 |

### 3.3 error:动作匹配 + 过纠

设 `E` 为期望动作集合,`P` 为预测动作集合,`M = E ∩ P`:

- **修正动作匹配率** `match_rate = |M| / |E|`(召回口径,支持部分匹配)。
- **动作完全匹配率** `exact_all_rate = (P == E 的条目数) / 可判分条目数`。
- **过纠率** `over_correction_rate`。单条判定为「过纠」当且仅当满足以下任一信号:
  1. **结构性过纠**:`P - E ≠ ∅`(预测做了期望之外的动作);
  2. **内容性过纠(把正确记忆改错)**:已给出 `correct_mem` 与 `predicted_corrected_memory` 时,
     二者字符二元组相似度 `< 0.5`(即修正结果偏离黄金正确记忆过大)。

> 过纠率为**结构性代理指标**,对相似度阈值敏感,详见「已知限制」。

---

## 4. 如何使用 / 接入真实召回、纠错管线

工具把「判分」与「产线」解耦。真实管线通过**插件**接入,插件实现两个抽象基类之一:

```python
# my_pipeline.py
from memory_eval import Recaller, Fixer

class MyRecaller(Recaller):
    def recall(self, question):          # question: dict,含 id/question/type/difficulty/text
        # 在这里接入真实召回 + LLM:检索记忆、生成答案
        return "你的预测答案"

class MyFixer(Fixer):
    def fix(self, item):                 # item: dict,含 id/input/wrong_mem/expect_action 等
        # 在这里接入真实纠错:判定错误、选择修正动作、给出正确记忆
        return {"actions": ["按时间更新"], "corrected_memory": "...", "reasoning": "..."}
```

用 `--plugin` 让 `score` 现场调用,而不是手工填写 `predictions`:

```bash
python memory_eval.py score --plugin my_pipeline.py --gold gold_causal.jsonl \
    --schema causal --out scores_causal.json
python memory_eval.py score --plugin my_pipeline.py --gold gold_error.jsonl \
    --schema error --out scores_error.json
```

要点:
- 插件文件名任意,只要包含 `Recaller` / `Fixer` 的继承类;多类同存时用 `--recaller-class` / `--fixer-class` 指定。
- 插件只接收可观察输入白名单：召回为 `id/schema/question/type/difficulty`；纠错为 `id/schema/input/wrong_mem/type/difficulty`。标准答案、期望动作和正确记忆只在评分端使用，不会传入插件。
- `recall()` 返回字符串;`fix()` 返回 dict,`actions` 用固定词表 token,`corrected_memory` 为修正后记忆。
- 未实现的插件(`raise NotImplementedError("M0 尚未接入…")`)会被**逐个条目告警并跳过**,不影响整体运行。
- 骨架流程:`convert` 产出黄金 → `dryrun` 产出模板 → 人工/插件填预测 → `score` → `report`。

---

## 5. 已知限制

1. **自动判分为规则化启发式,对 LLM 输出敏感。**
   - Top-1/Top-3 命中依赖 `texts_match`(归一化 + 包含 + 二元组 Jaccard ≥ 阈值),属于**近似/子串判分**,
     **不进行语义理解**。真实答案表述差异大时,命中率会被高估或低估。
   - 因果结构判定(「当时/后来」)与反事实三要素预检均为**字面规则**,不是语义判断。
   - **建议**:接入真实召回/纠错管线后,对结果**人工抽检约 20%**,以 `score` 的清单/报告为参照
     修正阈值或补充人工规则。
2. **过纠率是结构性代理指标。** 它综合「动作集合超出期望」与「修正后记忆与黄金记忆相似度 < 0.5」两个信号;
   相似度阈值敏感,`correct_mem` 字段缺失时会退化(只按动作超集判定)。属参考值,不替代人工复核。
3. **解析器覆盖两种已知版式**:逐字段分行 bullet 式(如 C01b),与「一条一行式」紧凑格式。
   - 无法解析的行会**告警并计数,不中断**;请留意 `convert` 的警告数是否偏离预期(应接近 0)。
   - 若评测集出现新版式,需扩展 `QA_ALIASES` / `ERROR_ALIASES` 或 `boundary_kind` 的边界规则。
4. **schema/字段推断带有启发式兜底**。`type`/`part`/`difficulty` 等缺失时由 id 前缀或字段推断,
   若与人工标注冲突,请以字段为准(字段优先)。
5. **不调用 LLM、不联网、无第三方依赖**。只提供**结构 + 统计**判分;语义级「对不对」仍需人,或把判分逻辑
   移到插件里用真实模型完成。

---

## 6. 交付清单

```text
memory_eval.py          评测工具(单文件,纯标准库)
memory_eval_README.md   本说明
```

常用命令(可在工作区直接运行):

```bash
python memory_eval.py convert "C01b_因果扩展集.md" --schema causal --out gold_causal.jsonl
python memory_eval.py dryrun --gold gold_causal.jsonl --schema causal --out pred_causal.jsonl
python memory_eval.py score pred_causal.jsonl --schema causal --gold gold_causal.jsonl --out scores_causal.json
python memory_eval.py report scores_causal.json --out report_causal.md
```
