# JVS — 时序-因果知识图谱与跨会话因果问答引擎 (v2.0)

[English](README.md) | [简体中文](README.zh-CN.md)

[![JVS CI](https://github.com/shenhui-ui/jvs-causal-graph/actions/workflows/ci.yml/badge.svg)](https://github.com/shenhui-ui/jvs-causal-graph/actions/workflows/ci.yml) ![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

JVS 是一个轻量级、本地优先的**时序-因果知识图谱**与**跨会话因果问答（Causal QA）**引擎，专为个人 AI Agent 设计。不同于常规 RAG 或扁平化的 Agent 记忆存储，JVS 在架构上强制执行以下机制：

1. **有向因果本体与置信度闸门**：以结构化事件（`subject / action / object / before / after / reason / ts / source_hash`）为节点，通过带方向和强度分级（`strong`、`weak`、`pending`）的因果边连接；低置信度（`<= 0.6`）边会被自动标记为 `pending` 仅作一跳线索，禁止污染多跳因果推理。
2. **双态双向因果遍历（Two-State BFS）**：在双向 BFS 中分离 `clue_seen` 与 `expanded` 状态，确保弱 1-hop 线索永远不会阻断另一条更强的多跳因果链。
3. **M1-11 优雅降级**：由 `assets/fallback.yaml` 驱动自动判别证据不足场景（`no_chain`、`low_confidence`、`weak_only`），防止模型在缺乏证据时编造因果结论。
4. **Fail-Closed 出站隐私闸门（PrivacyGate）**：在调用任何 LLM 或 Embedding 接口前，自动检测所有外发文本中的已知人名（Unicode NFKC + `casefold` 规范化）、凭据密钥与个人敏感信息（PII），命中即阻断。
5. **零外部服务依赖**：仅依赖单个本地 SQLite 文件，内置 **FTS5 trigram** 全文检索、可选 **sqlite-vec** 向量检索、**NetworkX** 图结构分析、**Typer** CLI 以及 **MCPServer**（`mcp >= 2.0`）。

---

## 架构总览

```mermaid
flowchart LR
    A["原始笔记 / 结构化事件"] --> B["PrivacyGate (Fail-Closed)"]
    B --> C["SQLite FTS5 + sqlite-vec RRF"]
    C --> D["因果图谱 (strong / weak / pending)"]
    D --> E["双态双向 BFS 遍历"]
    E --> F["M1-11 优雅降级"]
    F --> G["CLI / Python SDK / MCP 2.x Server"]
```

关于分层依赖边界、置信度闸门规则及评测复现保障的详细设计，请参阅 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 快速开始

### 1. 安装与环境配置

通过 pip 安装（PyPI 包名：`jvs-causal-graph`，需 Python 3.11+）：

```bash
pip install jvs-causal-graph
# 启用 sqlite-vec 向量检索与 MCP 2.x Server：
pip install "jvs-causal-graph[vec,mcp]"
```

如需本地源码开发：

```bash
pip install -e ".[all]"
```

复制 `.env.example` 为 `.env` 并填写模型配置：

```bash
cp .env.example .env
# 设置 JVS_LLM_PROVIDER (openai | deepseek | doubao | sensenova | ollama | custom)
# 以及 JVS_LLM_API_KEY
```

### 2. 1 分钟离线体验（`examples/`）

无需配置任何 API Key，即可使用 `examples/` 下的脱敏示例数据集完整跑通入库、多跳因果溯源、离线问答与规则评分：

```bash
jvs graph init --db demo.db
jvs graph ingest --db demo.db --events examples/events.jsonl --edges examples/edges.jsonl
jvs graph stats --db demo.db
jvs graph why EX1 --db demo.db --max-hops 3
jvs qa ask "为什么把数据库连接池从20扩容到60？" --db demo.db --offline
jvs qa run --db demo.db --questions examples/questions.jsonl --out demo_out --offline
jvs eval score --answers demo_out/answers.jsonl --questions demo_out/gold.jsonl
```

### 3. CLI 常用命令（`jvs`）

```bash
# 查看图谱统计与检索（兼容只读历史 SQLite 数据库）
jvs graph stats --db jvs_causal.db
jvs graph search "连接池扩容" --db jvs_causal.db --k 5
jvs graph why E1 --db jvs_causal.db --max-hops 3
jvs graph timeline --db jvs_causal.db --subject "连接池配置"

# 从原始文本抽取事件与因果边（经过 LLM + PrivacyGate）
jvs graph ingest-text --db jvs_causal.db --file notes.md --corpus worklog

# 或分阶段执行确定性管线
jvs pipeline extract --file notes.md --out events.jsonl
jvs pipeline candidates --events events.jsonl --out pairs.jsonl
jvs pipeline judge-edges --events events.jsonl --candidates pairs.jsonl --out edges.jsonl
jvs graph ingest --db jvs_causal.db --events events.jsonl --edges edges.jsonl

# 单题问答与批量评测
jvs qa ask "为什么扩容连接池？" --db jvs_causal.db
jvs qa ask "为什么扩容连接池？" --db jvs_causal.db --offline
jvs qa run --db jvs_causal.db --questions questions.jsonl --out out/
jvs qa prompt --db jvs_causal.db --questions questions.jsonl --out prompts_out/

# 评分与多轮稳健性读数
jvs eval score --answers out/answers.jsonl --questions questions.jsonl
jvs eval judge --answers out/answers.jsonl --questions questions.jsonl --out out/scores.json
jvs eval readout judge-result.e1.md judge-result.e2.md judge-result.e3.md

# Fail-Closed 隐私与凭据扫描
jvs privacy scan ./workspace --names names.json

# 启动 MCP Server（支持 stdio / sse / streamable-http）
jvs mcp serve --db jvs_causal.db
```

### 4. Python SDK（`CausalEngine`）

```python
from jvs import CausalEngine, JVSSettings

with CausalEngine("jvs_causal.db", settings=JVSSettings()) as engine:
    engine.init()
    hits = engine.search("连接池扩容", k=5)
    traversal, assessment = engine.why("E1")
    answer = engine.ask("为什么扩容连接池？", use_llm=False)
```

### 5. MCP 集成（OpenClaw / Claude Code / Cursor / Antigravity）

将 JVS 注册为本地 stdio MCP Server：

```json
{
  "mcpServers": {
    "jvs-causal-graph": {
      "command": "jvs",
      "args": ["mcp", "serve", "--db", "/path/to/jvs_causal.db"]
    }
  }
}
```

暴露的 MCP 工具：`causal_search`、`causal_why`、`causal_timeline`、`causal_ask`、`causal_ingest_event`、`causal_link_events`、`causal_graph_stats`，以及资源 `jvs://graph/summary`。

---

## 项目结构

```text
jvs-causal-graph/
├── LICENSE                  # MIT 许可证
├── README.md                # 英文文档
├── README.zh-CN.md          # 简体中文文档
├── pyproject.toml           # 包配置、依赖与 pytest / ruff 规则
├── .env.example             # 安全环境变量模板
├── .github/workflows/       # GitHub Actions CI 与 PyPI OIDC 发布工作流
├── assets/                  # 版本化提示词与 M1-11 fallback.yaml
├── examples/                # 脱敏可运行示例数据集
├── src/jvs/
│   ├── assets/              # 随 Wheel 打包的内置提示词与 fallback.yaml
│   ├── config.py            # pydantic-settings 配置与厂商预设
│   ├── domain/              # 纯 Pydantic 模型、因果规则、双态 BFS、NetworkX 分析
│   ├── infrastructure/      # PrivacyGate、SQLite+FTS5+sqlite-vec 存储、混合 RRF 检索、OpenAI+Tenacity
│   ├── application/         # 抽取、问答编排、评测、CausalEngine 外观层
│   ├── cli/                 # Typer + Rich 命令行
│   └── mcp/                 # MCPServer (mcp >= 2.0)
├── tests/                   # 离线 pytest 测试套件
├── scripts/
│   └── check.py             # 统一质量门禁（ruff + pytest + 卫生扫描）
└── docs/
    └── ARCHITECTURE.md      # 技术架构与核心设计决策
```

随时运行质量门禁：

```bash
python scripts/check.py
```

---

## 许可证

本项目基于 [MIT License](LICENSE) 开源。
