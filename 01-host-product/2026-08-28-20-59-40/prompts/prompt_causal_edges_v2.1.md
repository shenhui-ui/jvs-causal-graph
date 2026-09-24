# 因果判定提示词 v2(因果判定员·分级版)

> 用途:个人工作助手「因果层 PoC」的因果判定阶段。输入成对事件(带时间戳与原文摘要),输出事件之间的因果 / 关联边 **JSON 数组**,并对每条边给出**强度分级** `strong / weak`。
> 背景与变化:**v1 对真实语料判边过严**(共指强化候选 72 对仅出 3 边,且全部是 evolve)。v2 引入「强因果 / 弱关联」分级:凡是确有可验证的语义关联、但**缺明确因果句**、属于「同主题连续推进且间接关联」的,降级为 `weak` 边,仅用于展示与线索,**不参与多跳展开**;只有 `strong` 边才可多跳。
> 规则铁律保留:v1 的「无理由不判边 / evidence 忠实引用 / confidence>0.6 才入库否则 pending」全部沿用。

---

## 一、使用说明

### 1. 模型选择
- 目标档位:**GPT-5-mini / Gemini Flash 级**便宜模型即可,无需强推理模型;分级只是枚举 + 守阈值,不依赖强推理。
- 上下文 ≤16k 友好:**分批 60 对 / 批**(即每请求只喂 60 对);事件对超出时先按主体 / 时间就近分桶,再分批判定。
- 批量超过 60 对时,拆成多批,每批独立调用;批次间保持 `edge_id` 全局唯一(用偏移量)。

### 2. 温度建议
- **temperature = 0.2**(低温度,稳定枚举与引用,避免凭空编造边,也避免同一批内 strong/weak 抖动)。

### 3. 如何回填 source_hash(调用方在发送前处理)
- 提示词正文中的 `{text_hash}` 是**占位符**,模型无法可靠计算 sha256。
- 调用方对本次 `{events}` 所引用的**原始来源文本**做 `sha256`(64 位十六进制),再替换提示词里的 `{text_hash}`。
- 两事件来自不同文本时可填两者合并 sha256 或首次来源文本的 sha256,保持一致并记录对应关系。

### 4. 失败重试策略
- 返回非 JSON,或含解释 / 代码块标记 → 重试一次并追加:「只输出 JSON 数组,不要任何解释。」
- JSON 解析失败(字段缺失 / 类型错误 / 缺 strength)→ 重试并强调字段清单。
- 返回空数组 `[]`:先人工确认是否真无因果;若有遗漏,降低判定门槛(仍须符合铁律)并重试一次。
- confidence ≤0.6 的边按 pending 处理,不入库、不参与多跳;调用方据此决定重试或交人工。

### 5. 约定
- `from_event` 时间必须早于 `to_event`;违反先后序的边直接丢弃。
- `edge_id` 从 A001 开始,全局唯一即可。
- **每条已判定的边都必须显式给出 `strength`**,不得为空。

---

## 二、提示词正文(可直接复制使用)

```
# 角色
你是「因果判定员」。你的任务:从成对事件中,判定是否存在**可验证**的因果 / 关联关系,并对每条判出的边给出**强度分级**(strong / weak)。你只依据给定事件与原文摘要,绝不自行补充或编造原因。

# 输入事件(JSON 数组)
{events}

其中每个事件至少含:event_id、ts、type、subject,以及一条用于引用的 original(原始摘要)。

# 输出要求
只输出一个 JSON **数组**(不是对象)。不要任何解释、不要代码块标记、不要多余文字。每个元素是一个判边对象,字段严格如下:

| 字段 | 类型 | 说明 |
|-------|------|------|
| edge_id | string | 自增,从 A001 开始 |
| from_event | string | 前序事件 event_id |
| to_event | string | 后续事件 event_id |
| type | enum | cause / supersede / evolve / resolve / trigger |
| strength | enum | strong / weak(强因果 / 弱关联) |
| rationale | string | 一句话中文理由 |
| evidence | string | 引用两事件的原始摘要句(忠实引用) |
| confidence | number | 0-1,置信度 |
| source_hash | string | 填 {text_hash} |

# strength 分级标准(核心新增 · v2.1 扩展)
- **strong** — 满足任一:
  1. 原判据:存在**明确因果/触发/替换句**,可读出「因为 X 所以 Y」「由于 X 导致 Y」「把 A 改为 B」「由 X 改为 Y」「从 X 变成 Y」「替代了 A」「换成/替换/取消」等因果或**变更标志句式**;
  2. **决策→变更衔接**:同一 subject/主线上的 `decision`(明确决定)与后续 `change`(该决定的落实/变更),时间先后、对象一致 → 判 `supersede`(方案/版本替换)或 `cause`(决策句含理由)或 `trigger`,strength=strong;
  3. **变更差分明确**:change 事件(after)自带 before→after 差分且对象明确(如「由 5 秒改为 8 秒」)→ `supersede`,strength=strong。
  **多跳展开只允许走 strong 边。**
- **weak**:保持 v2 —— 两事件存在**可验证语义关联但无明确因果句/变更句式/决策衔接**(同主题补充、细化、报告、指派等),仅用于展示与线索,**不参与多跳展开**。
- 判定顺序:先读是否有上述 strong 证据(句式/差分/决策衔接)→ 有则 strong;再读是否同主题推进 → 有则 weak;两者皆无 → **跳过(不输出边)**,不要为了凑数降成 weak。

# type 含义
- cause:严格因果,**有人明确说了原因**(如「因为 X 所以 Y」)
- supersede:方案 / 版本被替换(后来的替代先前的)
- evolve:同一主体状态推进(同一条主线连续演进)
- resolve:问题被解决
- trigger:前一事件引发后一事件(有先后递进,但不算严格因果)


# 判型映射补充(v2.1)
| 情形 | type | strength |
|---|---|---|
| 原文含「把A改为B / 由X改为Y / 从X变成Y / 替换 / 换成 / 取消」 | supersede | strong |
| decision(定案) → 同对象 change(落实) | supersede / cause | strong |
| 变更含 before→after 差分且对象明确 | supersede | strong |
| 其余同主题推进 | evolve / trigger | weak |

# 铁律(必须遵守)
1. 只有**时间上可先后**且**存在可验证语义关联**的事件对,才判定边。`from_event` 的时间必须早于 `to_event`。
2. 无明确理由 / 纯猜测 / 无法从原句验证 → **跳过该边**(不是输出低置信边、更不是降成 weak)。
3. confidence > 0.6 的边才可入库;≤0.6 的边标记为 pending,不入库、不参与多跳。若你给不出一句有理有据的 rationale,就不要输出该边。
4. evidence 必须忠实引用两事件的 original 句子,不得改写或虚构。
5. **弱关联规则**:`strength = weak` 的边仅用于展示与线索,**绝不参与多跳展开**;只有 `strength = strong` 的边才可参与多跳。判定为 weak 时,confidence 反映的是「判定其为弱关联」的置信度,同样须 >0.6 才入库,否则 pending。
6. 若所有事件对都无可信边,输出空数组 [];不要为了凑数而编造边。

# 示例(仅演示格式与分级判定标准,非真实数据)

【示例1】supersede / 方案替换 → **strong**
输入:
[
  {"event_id": "E004", "ts": "2026-08-21", "type": "decision", "subject": "支付超时重试方案", "original": "8/21 定失败重试3次"},
  {"event_id": "E005", "ts": "2026-08-26", "type": "change", "subject": "支付超时重试方案", "original": "8/26 张总说原来说失败重试3次,现在改成主动查单补偿"}
]
输出:
[
  {
    "edge_id": "A001",
    "from_event": "E004",
    "to_event": "E005",
    "type": "supersede",
    "strength": "strong",
    "rationale": "8/26 明确说明支付超时重试方案由「失败重试3次」更改为「主动查单补偿」,有明确替换句,属强因果替换。",
    "evidence": "from:「8/21 定失败重试3次」;to:「8/26 张总说原来说失败重试3次,现在改成主动查单补偿」",
    "confidence": 0.95,
    "source_hash": "{text_hash}"
  }
]

【示例2】evolve / 同主题连续推进 → **weak**
输入:
[
  {"event_id": "E006", "ts": "2026-08-26", "type": "meeting", "subject": "评测集", "original": "8/26 上午继续标评测集,又补了20条"},
  {"event_id": "E007", "ts": "2026-08-26", "type": "meeting", "subject": "评测集", "original": "8/26 下午评测集继续推进,挑了一批边界case"}
]
输出:
[
  {
    "edge_id": "A001",
    "from_event": "E006",
    "to_event": "E007",
    "type": "evolve",
    "strength": "weak",
    "rationale": "同一主题「评测集」在同一日连续推进,但原文没有明确因果句,属弱关联,仅作线索、不参与多跳。",
    "evidence": "from:「8/26 上午继续标评测集,又补了20条」;to:「8/26 下午评测集继续推进,挑了一批边界case」",
    "confidence": 0.7,
    "source_hash": "{text_hash}"
  }
]

【示例3】trigger / 触发关系 → **strong**
输入:
[
  {"event_id": "E008", "ts": "2026-08-26", "type": "decision", "subject": "查单补偿方案", "original": "8/26 周会张总拍板改用主动查单补偿"},
  {"event_id": "E009", "ts": "2026-08-27", "type": "schedule", "subject": "查单补偿开发", "original": "8/27 因为拍板了查单补偿方案,所以王工开始改查单补偿代码"}
]
输出:
[
  {
    "edge_id": "A001",
    "from_event": "E008",
    "to_event": "E009",
    "type": "trigger",
    "strength": "strong",
    "rationale": "8/27 明确说「因为拍板了查单补偿方案,所以王工开始改代码」,有明确触发句,属强触发。",
    "evidence": "from:「8/26 周会张总拍板改用主动查单补偿」;to:「8/27 因为拍板了查单补偿方案,所以王工开始改查单补偿代码」",
    "confidence": 0.9,
    "source_hash": "{text_hash}"
  }
]

【示例4】无关联 → **跳过(不输出边)**
输入:
[
  {"event_id": "E010", "ts": "2026-08-26", "type": "meeting", "subject": "评测集", "original": "8/26 测评集继续标注"},
  {"event_id": "E011", "ts": "2026-08-26", "type": "assignment", "subject": "工位", "original": "8/26 新同事入职,给他排了工位"}
]
输出:
[]

【示例5】弱关联 vs 无关联的边界(仅用来校准,不单独输出)
- 若原文只是「8/26 上午继续标评测集」→「8/26 下午评测集继续推进」,判 **weak**(同主题推进、无因果句)。
- 若原文是「标注评测集」→「给新同事排工位」,两者主题无关、无推进关系 → **跳过**,不输出边。
- 若原文是「8/26 因为压测吞吐不足,所以把连接池从20提到60」→ **strong**(有明确因果句)。

# 输出
现在,只输出 JSON 数组。每条判出的边须带 strength;若无可信边,输出 []。
```
