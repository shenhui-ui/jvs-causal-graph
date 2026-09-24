# M1-08 / M1-09 / M1-10 运行说明（因果层 PoC）

本目录包含离线预处理、抽取回归、图谱问答和真实模型生成/判分工具。
`s4_pipeline.py`、`extract_regression.py` 和回归测试不调用模型；`llm_answer.py`、`llm_judge.py` 会请求配置的模型服务。
真实调用前确认 provider、凭证、配额及隐私闸门；输出写入独立目录，不覆盖冻结实验结果。

---

## 一、文件清单

| 文件 | 阶段 | 作用 |
|------|------|------|
| `s4_pipeline.py` | M1-08 | 预处理/调度总控:`candidates` / `batches` / `merge` / `report` 四个子命令 |
| `extract_regression.py` | M1-09 | 抽取回归:读 `s3_*` 批结果,输出 `regression-report.md` |
| `llm_answer.py` | M1-10 | 图谱检索后调用模型生成答案；`chat()` 提供共享传输层 |
| `llm_judge.py` | M1-10 | 真实模型语义判分、成功断点复用、错误分类及固定 gold 分母 |
| `test_llm_judge.py` | 回归 | 纯离线验证判分、重试、断点、隐私和超时边界 |
| `README-run.md` | — | 本说明 |

---

## 二、M1-08 预处理/调度流水线

流程:`candidates` → `batches` → (外部调用判定提示词写回包) → `merge` → `report`。

### 1. 生成候选对(→ §2.3 判定输入)

```bash
python s4_pipeline.py candidates --events all-real-events.jsonl \
    --out candidates-m1.jsonl --max-candidates 600 --window-days 7
```

- 读 393 真实事件,生成四通道候选:
  - ① 邻接:同 corpus、时间差 ≤ ±7d 且 from.ts ≤ to.ts;
  - ② 共指:主体/对象规范化后字符重叠(共享主体权重最高);
  - ③ 语义:从事件 `after` 提取关键词的 2-gram 重叠(Jaccard ≥ 0.20);
  - ④ 语义嵌入:本机 Ollama `bge-m3` 批量嵌入 → 余弦相似(≥ `--emb-threshold`,默认 0.6,
    以 change/bugfix 为中心取 top 12)。**需要本机 Ollama 服务**;不可用时自动降级为③
    并在 stderr 打 WARN(不再静默)。
- 合并去重排序,`channels` 字段记录该候选命中哪些通道,`score` 为排序分;
- 默认规模 **300~800**(默认 `--max-candidates 600`;若候选池本身 <300 会据实输出并告警)。

### 2. 分批(60 对/批,供 M1-07 提示词组装)

```bash
python s4_pipeline.py batches --candidates candidates-m1.jsonl \
    --events all-real-events.jsonl --from 0 --to 600 \
    --dump s4_batches --manifest s4_batches_manifest.json
```

- 把候选全局区间 `[from, to)` 切成 60 对/批;
- 输出:
  - `s4_batches_manifest.json`:分批清单(每批 batch_no / from / to / count / text_hash / 文件路径);
  - `s4_batches/batch_NNN.payload.json`:每批**与 M1-07 提示词组装所需 json**
    (`{pair_no, from_event, to_event}` 摘要 + `text_hash` 占位)。
- 终端打印一张分批表格(第 N 批、pair 区间、对数、hash)。
- **模型回包**:把判定结果(raw 文本)保存为 `s4_batches/batch_NNN.result.txt`(用 `--dump` 目录)。低置信或 pending 边可作为当前节点的一跳展示线索，但不得把目标节点加入下一跳扩展；`report` 的 pending 统计应先完成人工复核。SQLite 图谱统一使用 `events.id`，管线加载时同时提供兼容字段 `event_id=id`。时间邻居使用相同 `corpus` 隔离；旧库无 `corpus` 时按相同且非空的 `source_hash` 保守隔离，作用域不明时不跨来源混配。

### 3. 合并批结果(按 (from,to) 去重 + edge_id 全局重排)

```bash
python s4_pipeline.py merge --dump s4_batches \
    --pattern '*.result.txt' --out all-real-edges-m1.jsonl
```

- 遍历 `s4_batches/`,对每个结果文件用 `extract_array`(括号配对)从含日志的回包中
  定位 JSON 数组;
- 合并 → 按 `(from_event, to_event)` 去重(保留先出现者)→ `edge_id` 全局顺序重排
  `A001` 起 → 写 `all-real-edges-m1.jsonl`。

### 4. 边统计 + 编造抽检清单

```bash
python s4_pipeline.py report --edges all-real-edges-m1.jsonl \
    --candidates candidates-m1.jsonl --manifest s4_batches_manifest.json \
    --events all-real-events.jsonl --audit-out s4_audit_pairs.jsonl
```

- 统计:总数 / 分 type / 分 strength / weak 占比 / pending 数;
  - `strength` 由 `confidence` 推断:`>0.8`→strong,`>0.6`→medium,`≤0.6`→weak;
  - pending = `confidence ≤ 0.6`(与提示词铁律一致,不参与多跳)。
  - ⚠️ 上表是 **`report` 子命令的统计口径**,不是入库口径。**入库时以 `causal_graph.py`
    的闸门为准**(见下),二者对同一 `confidence` 的标签取值**可以不同**:
    `report` 生成 `medium`,而**入库只写 `strong`/`weak`/`pending`**(`medium` 被压平为 `strong`,
    以对齐 `_edge_expandable` 的「非 weak/pending 即可展开」)。

### 入库闸门（`causal_graph.py`，2026-09-22 补）

边写入 `causal_edges` 前按 `confidence` **归一 `strength`**，判定与 `_edge_expandable`
**共用同一事实源**（`confidence` 为准，`strength` 只是展示标签）：

| `confidence` | 入库 `strength` | 能否继续多跳 |
|---|---|---|
| 不可解析 / `IS NULL` | `pending` | 否 |
| `≤ 0.6` | `pending` | 否（但**仍入库**，可作「一跳线索」） |
| `> 0.6` | `strong`（或原 `weak`） | `strong` 是 / `weak` 否 |

- **只改标、不剔边**：低置信边仍入库，以保留 `llm_answer` 所需的「一跳线索」用途
  （该模块明文：「保留低置信边作为当前节点的一跳线索」）。
- 关闭开关：环境变量 **`JVS_CONF_GATE=0`** 退回旧行为（不改标）。
  ⚠️ **历史重建（`apply_arbitration_146/20.py`）必须显式设 `JVS_CONF_GATE=0`** ——
  它们的校验断言「库内 `strength` == 边集 `strength`」，闸门开启时会报 1,359 条不一致
  （实测），那属于**当代口径 vs 历史口径**的差异，不是缺陷。两个脚本已内置该设置。
- 统计输出字段：`relabeled_pending` / `gated_conf_none` / `gated_strength_conflict` /
  `gated_strength_missing`。其中 `gated_strength_conflict` 计数**边集里 `strength` 与
  `confidence` 自相矛盾**的边（如 `strength=strong` 但 `confidence≤0.6`）——这类边在改标前
  是「带 strong 标签参与多跳」的，是**真缺陷面**，只告警不阻断。
- 回归件：`test_causal_graph.py`（已挂入 `scripts/git-gate.sh` 第 3 步）。
- 编造抽检清单:按批归属,每批随机抽 12 对(种子可复现),连同 from/to 原始摘要与
  判边字段写 `s4_audit_pairs.jsonl`,供人工判定「语料真实 → 过提取 / 语料无依据 → 编造」。

### 验收对应(→ §2.3)

M1-08 的输出(`candidates-m1.jsonl` → `all-real-edges-m1.jsonl` → `s4_audit_pairs.jsonl`)
正是 **因果判定(§2.3)** 的输入与质量守门:
- `batches` 产出的 payload 就是喂给判定提示词的成对事件;
- `merge` 产出的边数组就是判定阶段的「边」结果;
- `report` 的统计与抽检清单用于 §2.3 的编造/弱边人工复核。

---

## 三、M1-09 事件抽取回归

### 运行

```bash
python extract_regression.py --base . --pattern 's3_*.txt' \
    --events all-real-events.jsonl --gold gold-events-real-50-init.jsonl \
    --out regression-report.md
```

- `--pattern`:批结果文件通配(可逗号分隔多模式),默认 `s3_*.txt`;
  - 绝对路径或含分隔符时按字面 glob,否则相对 `--base` 检索。
- 每个文件调用与 `s4_pipeline` 同源的 `extract_array` 定位 JSON 数组;
- `--gold`(默认 `gold-events-real-50-init.jsonl`)**:存在才核对**,缺失则跳过并说明。

### 结论字段(regression-report.md)

1. **事件数**:抽取事件总数(按内容指纹去重)+ 各批文件抽取数(去重前);
2. **与既有 393 的 diff(内容指纹)**:命中 / 新增(抽取到但 393 无)/ 缺失(393 有但未抽到),
   各给前 50 条清单;
3. **判定表命中**:若金标准存在,做 v3 口径核对(2-gram + after,参照
   `tools\causal\score_recall.py`):
   对每个金标准在抽取集中找候选(ts 相同 + 主体/对象命中,或 after 相似 ≥ 0.35),
   按候选数升序确定性贪心分配,输出 **命中 / 漏抽 / 粒度合并 / 过提取** 判定表,
   并给出召回率(按事实槽)与精确率。

---

## 四、py_compile 校验

```bash
python -m py_compile s4_pipeline.py extract_regression.py llm_judge.py test_llm_judge.py
python -B test_llm_judge.py
```

回归夹具使用系统临时目录并注册自动清理，不写入源码目录；测试会阻止真实网络请求，不读取生产图谱或凭证。

## 五、M1-10 真实判分、错误与断点

以下命令中的 `python` 代指隔离环境的实际解释器。先确认模型配额及脱敏，配置凭证不写进命令、日志或报告：

```bash
LLM_PROVIDER=sensenova LLM_JUDGE_MAX_TOKENS=4096 python llm_judge.py \
    --answers answers.jsonl --gold gold.jsonl --out scores-sn-new.json --parse-retries 1
```

### 输入与断点

- 输入支持 JSONL、JSON 数组和含 `items` / `answers` / `results` 数组的完整 JSON。gold qid 是固定分母；可传全量答案配合子集 gold，只判 gold 内题目。
- 判分正文只接受完整 JSON 对象或完整 Markdown 代码围栏中的 JSON 对象；不从说明文字、数组或损坏 JSON 中抽取内部对象。重复键、超深嵌套、超长整数等解码错误按 `response_invalid_json` 有界处理，不中断整批。
- `cause`、`effect`、`evidence_hint`、`available` 必须为真正 JSON 布尔，拒绝字符串、数字和嵌套结果。
- 缺失/空答案为 `answer_missing`；显式非真 `pipeline_ok` 为 `answer_failure`；非字符串答案为 `answer_invalid`。这些题不请求模型，但仍以失败占位计入 gold 分母。旧答案未提供 `pipeline_ok` 时保持兼容，只按文本有效性判定生成状态。
- 中间结果写入 `<out>.checkpoint.jsonl`，最终 `<out>` 是汇总 JSON，两者都采用临时文件原子替换。失败记录供诊断，下次不复用。
- 仅复用 `judge_ok is True`、四个布尔字段合法、无错误类型且指纹匹配的记录。合法的 `available=false` 也可复用，它是语义判断成功而非请求失败。
- 指纹绑定题目、gold、答案正文、答案状态、实际 provider/model、端点和判分提示配置；凭证不参与指纹。端点只在结果元数据中保存 SHA-256，不保存 URL 中可能存在的凭证。旧版只记录环境模型名的指纹不会被误当作当前有效配置；升级后请用新输出路径，不要直接重跑冻结结果路径。
- 每次落盘保留尚未遍历的成功断点；中途退出不会因较早 qid 重判而丢失后序成功项。仅延迟改变时刷新 `latency_ms`，不重复语义判分。

### 有界重试与错误类型

| `error_type` | 含义 | 判分层追加重试 |
|---|---|---|
| `response_empty` | chat 返回空内容 | 最多一次 |
| `response_invalid_json` | 判分正文不是合法 JSON | 最多一次 |
| `response_schema_invalid` | 返回结构或四个布尔字段非法 | 不追加 |
| `api_429` | 底层退避后仍限流 | 不追加 |
| `api_5xx` | HTTP 5xx | 不追加 |
| `api_4xx` | 非 429 的 HTTP 4xx | 不追加 |
| `api_timeout/network` | 超时、DNS、连接等错误 | 不追加 |
| `api_unknown` | 未分类调用失败 | 不追加 |
| `answer_missing` / `answer_failure` / `answer_invalid` | 输入答案不可判 | 不发请求 |

`--parse-retries` 只支持 0 或 1，默认 1；重试使用同一提示与参数，间隔为 `LLM_GAP`（默认 16 秒）。成功返回 `error_type=""`，重试前的错误留在 `attempt_errors`；`judge_attempts` 统计 chat 调用次数，`parse_retry_count` 统计额外正文解析尝试。

传输层 `llm_answer.chat()` 的现行策略：单次请求 timeout 为 360 秒；首次请求之外，429 最多重试 6 次，每次等待 66 秒；非 HTTP 异常最多重试 6 次，每次等待 10 秒；5xx 和非 429 的 4xx 立即向上传播。判分层不对 chat 抛出的异常再次重试，避免乘法式放大请求。HTTP 响应外壳的非法 JSON/结构异常由底层处理，与正文解析重试分开。上述次数是有限上界，不等于计划中的逻辑请求数；仍需在网络调用前确认配额。当前未实现 `Retry-After` 解析或 5xx 专门退避。

结果只持久化固定诊断消息与 HTTP 状态码，不写原始服务响应体、答案片段或 API key。`judge_ok=false` 的四个 false 是保守统计占位，不是四项真实语义反例；`unavailable_reason` 明确写判分未完成。只有合法的 `judge_ok=true, available=false` 才写“语义不可用”。

### 指标与超时

- 保留原有语义指标，增加 `generation_success_rate`、`judge_success_rate`、`metric_counts` 的分子/分母和 `error_counts`。
- 语义 `availability` 是有效判分且 `available=true` 的题数 / gold 总题数，判分失败不缩减分母。
- `llm_judge.py` 不施加答案耗时评分阈值，`meta.score_timeout_ms=null`；`judge_ms` 只是本次判分耗时。
- `python ../eval/eval_m1.py score answers.jsonl --gold gold.jsonl --timeout-ms 180000 --out scores-rule-180s.json` 是专家模式的独立规则观察示例，不改变默认 30 秒基线，也不改变 HTTP 请求 timeout。阈值须事前约定，结果必须与语义评分分文件、分口径展示。详见 [评测说明](../eval/eval_m1_README.md)。
- `eval_m1.py --double-check` 是规则通道两轮；它不是 `llm_judge.py` 的重试或真实模型双判分。

### 分通道实验限制

现有 `genN_bestof.py` 给生成和判分子进程传同一份环境，不用于跨 provider 的首轮定向实验。`targeted_round.py` 提供固定八题、单候选的 prepare / generate / judge 阶段；生成锁定 doubao_sub/doubao，判分锁定 sensenova/deepseek-v4-pro。不得把同模型自评或 gold 辅助选拔成绩当成盲测。

## 六、8 题首轮定向实验

当前首轮已完成，工作区 `outputs/m1-10-targeted-20260907-v3-r1/overview.md` 为结果入口：生成与有效判分均 8/8，三要素联合 0/8，无候选替换原答案；实际生成 HTTP 8 次、桥上游 8 次、判分 HTTP 8 次。原始 21/30 与仅合入 Q022 补判的 22/30 诊断分开保留。已发现检索缺口及标准答案引用/来源背景口径待核对项；当前仅做离线核对，不追加候选、复判或覆盖原结果。

- 固定 Q001/Q008/Q009/Q011/Q014/Q026/Q027/Q029，每题生成一次并判分一次；不自动追加候选或固定复核，后续需要单独授权。
- `LLM_PROMPT_EVIDENCE_FIRST=1` 启用通用三行约束及已召回事件的编号/出处对照；默认关闭。开关不改变检索或图边；编号索引是本轮提示变量的一部分，不声称只改一行指令。
- `prepare_question()` 复用现行检索流程。冻结 30 题 trace 与 `extract_keywords_v2()` 全部一致，邻居数为 0；首轮显式设置 EXT_V2=1，不把历史计划中“不额外开启”误解为关掉既有默认。准备阶段逐题比较关键词、锚点数、边数和邻居数。
- 准备阶段只向生成提示传 qid/question/type 与召回材料，gold 不进入生成提示。生成与判分提示均通过姓名、凭证、联系方式与链接模式闸门；命中即停止，不自动外发或自动改写 gold。
- 用 `verification-followup-final-20260907.json` 的冻结哈希作为 `--reference`；`--db/--questions/--gold/--base` 必须来自该记录，且为相同 30 题。`--work` 必须是新的空路径。准备结果包含脱敏检查后的提示、单独的本地 gold 子集、输入/源码哈希。

```bash
python targeted_round.py prepare --reference <冻结核验.json> --db <v3.db> \
    --questions <v1.jsonl> --gold <gold.jsonl> --base <冻结answers.jsonl> \
    --endpoint http://127.0.0.1:9093/v1/chat/completions --work <新实验目录>
# 独立桥进程配置：DOUBAO_TARGETED_ROUND=1 DOUBAO_SUB_THINK=3 DOUBAO_FRESH=1
# 以既有隔离解释器运行 aux_doubao_bridge.py <本地session路径> 9093
python targeted_round.py generate --work <新实验目录>
python targeted_round.py judge --work <新实验目录>
python -B test_targeted_round.py
```

- 必须使用专用空闲端口，不能接管既有桥。生成入口检查 health 的 expert/fresh/targeted 和零上游计数。`DOUBAO_TARGETED_ROUND=1` 时桥最多 8 次上游调用、禁止模式覆盖、关闭桥异常重试和验证码重试；生成 HTTP 超时不自动重发。默认桥行为不受该开关影响，日志不再写会话编号。
- 判分保留既有最多 6 次传输重试和最多 1 次正文重试。首轮“16 次”是逻辑题级调用，不是 HTTP 硬上限：状态单列 logical_calls/chat_calls/http_requests；判分 HTTP 上界 112。鉴权失败、非法 schema 或有界重试仍失败时停止后续题，不换 provider。
- 每阶段使用排他 started 标记、先记调用再发请求；中断后不能对同一目录自动继续，防止超时重复消费。重新启动须检查已有状态并另获重试授权，禁止删标记重跑。
- 生成完成后保存答案哈希，判分阶段核验；每阶段前后核验冻结输入，源码或准备材料变化也拒绝执行。原数据库、题集、gold、答案和评分不覆盖。
- 格式三行、编号属于本次材料、来源引用匹配分别记录；自动验证只证明引用存在于提供材料，不能证明因果支持性。生成失败或判分失败仍按固定 8 题分母报告；未尝试题明确标记 not_attempted。原始异常只记录固定类型，命中隐私的答案仅写 local-only 文件，不继续判分。
