# 脚本登记表 —— JVS / M1 跨会话因果问答

> 自动生成 + 人工登记 ｜ 生成时间：2026-09-28 14:10
> 目标：新会话接手时**不用翻目录就知道该跑哪个脚本**
>
> ⚠️ 本文件是**生成物** —— 手工编辑会被重跑抹掉。要改内容请改 `_migrate/make_scripts_md.py`（人工登记项在 `CORE` / `P03_FAMILIES` / 各 `for f, d in [...]` 列表里）。
> 全库 `.py` 的生命周期登记（活件 / 一次性 / 可弃）见 `_index/一次性脚本登记表.md`。

---

## 一、核心脚本（有明确入口，可直接跑）

路径基准：`01-host-product/2026-08-28-20-59-40/`

| 脚本 | 分类 | 进度 | 联网 | 作用 | 用法 |
|---|---|---|---|---|---|
| `tools/causal/causal_graph.py` ✅ | 因果图谱引擎 | 已开发 | 离线 | SQLite 图谱 `init` / `ingest` / why 查询 | `python causal_graph.py init && python causal_graph.py ingest` |
| `tools/causal/extract_regression.py` ✅ | 因果图谱引擎 | 已开发 | 离线 | M1-09 事件抽取回归：读 `s3_*` 批结果，出 `regression-report.md` | `python extract_regression.py --base . --pattern 's3_*.txt' --events all-real-events.jsonl --gold gold-events-real-50-init.jsonl --out regression-report.md` |
| `tools/causal/llm_answer.py` ✅ | 因果图谱引擎 | 已开发 | **调模型** | 图谱检索后调模型生成答案；`chat()` 提供共享传输层 | `被 eval 流程引用，一般不单独跑` |
| `tools/causal/llm_judge.py` ✅ | 因果图谱引擎 | 已开发 | **调模型** | 真实模型语义判分、成功断点复用、错误分类、固定 gold 分母 | `LLM_PROVIDER=sensenova LLM_JUDGE_MAX_TOKENS=4096 python llm_judge.py --answers answers.jsonl --gold gold.jsonl --out scores-sn-new.json --parse-retries 1` |
| `tools/causal/relay_call.py` ✅ | 因果图谱引擎 | 已开发 | **调模型** | 通用 OpenAI 兼容中转调用器（SenseNova 等）；key 走 `RELAY_KEY`/`PROBE_KEY` | `python relay_call.py --payload <p.txt> --out <r.txt> [--base ... --model ...]   |   python relay_call.py --probe` |
| `tools/causal/s4_pipeline.py` ✅ | 因果图谱引擎 | 已开发 | 离线 | 预处理/调度总控：`candidates` / `batches` / `merge` / `report` 四个子命令；candidates 产出四通道候选（邻接 / 共指 / 关键词 / 语义嵌入），嵌入通道走本机 Ollama `bge-m3`（不可用时降级并告警） | `python s4_pipeline.py candidates --events all-real-events.jsonl --out candidates-m1.jsonl --max-candidates 600 --window-days 7` |
| `tools/causal/targeted_round.py` ✅ | 因果图谱引擎 | 已开发 | **调模型** | 固定八题单候选定向实验：prepare / generate / judge 三阶段 | `python targeted_round.py prepare --reference <冻结核验.json> --db <v3.db> --questions <v1.jsonl> --gold <gold.jsonl> --base <冻结answers.jsonl> --endpoint http://127.0.0.1:9093/v1/chat/completions --work <新实验目录>` |
| `tools/causal/test_llm_judge.py` ✅ | 因果图谱引擎 | 已开发 | 离线 | 判分/重试/断点/隐私/超时边界回归（**不联网**，自检项之一） | `python -B test_llm_judge.py` |
| `tools/host/acp_bridge.py` ✅ | 宿主会话桥 | 开发中 | **调模型** | **ACP 会话桥**：用本机 DSH `--profile acp` 新建/列举/恢复会话并投递交接材料（方案 D）。零依赖，**内建强制隐私闸门**（材料全文内联、投递前自动过 `name_scan.py`、找不到闸门即拒发）。`probe`/`list` 只读；`new`/`resume` 会调模型。已注入 `PATHEXT` 修复接续侧 git 不可用 | `python acp_bridge.py probe ｜ list ｜ new --material <f> [--dry-run] ｜ resume --session-id <id> --material <f>（见 tools/host/README-acp-bridge.md）` |
| `tools/host/dup_recall_check.py` ✅ | 宿主数据采集 | 已开发 | 离线 | 重复召回检查 | `—` |
| `tools/host/host_memory_dump.py` ✅ | 宿主数据采集 | 已开发 | 离线 | 宿主记忆导出 | `见 tools/host/README-privacy.md` |
| `tools/host/m1_02_process.py` ✅ | 宿主数据采集 | 已开发 | 离线 | M1-02 采集记录处理 | `—` |
| `tools/host/name_scan.py` ✅ | 宿主数据采集 | 已开发 | 离线 | **外发隐私闸门**：命中真名即拒发。默认表为运作口径，勿用 `--names identities-check` 严格表 | `python name_scan.py <文件>（见 tools/host/README-privacy.md）` |
| `tools/eval/eval_m1.py` ✅ | 评测工具 | 已开发 | 离线 | 规则口径评分：默认 30s 基线，可 `--timeout-ms` 观察；`--double-check` 两轮 | `python ../eval/eval_m1.py score answers.jsonl --gold gold.jsonl --timeout-ms 180000 --out scores-rule-180s.json` |
| `tools/eval/make_smoke_set.py` ✅ | 评测工具 | 已开发 | 离线 | 生成 smoke 小样本集 | `见 tools/eval/eval_m1_README.md` |
| `tools/eval/memory_eval.py` ✅ | 评测工具 | 已开发 | 离线 | 记忆层评测 | `见 tools/eval/memory_eval_README.md` |

> ❌ 表示文件不存在，需核实；⚠️未入库 表示本机存在但 git 未跟踪（克隆副本上不会有）。

---

## 二、D10 复核机械脚本族（`03-d10-workspace/2026-09-08-20-30-19/`）

该目录下 **289 个顶层 `.py`**，按命名前缀分族。**它们互相 `import`，必须在本目录内运行**：

| 前缀 | 族 | 进度 | 作用 | 数量 |
|---|---|---|---|---:|
| `_p0*` | D10 复核·P0 体检族 | 已开发 | 产出物完整性/边界/类目枚举/回源定位 | 33 |
| `_r1[4-9]*` | D10 复核·修订轮脚本族 | 已开发 | r14→r19 各轮 rebuild / check / validate | 0 |
| `apply_*` | D10 复核·变更应用族 | 已开发 | 把裁定落盘到 review 产物 | 34 |
| `serialize_*` | D10 复核·序列化族 | 已开发 | 按 group/chunk 生成复核 json | 84 |
| `validate_*` | D10 复核·校验族 | 已开发 | 各轮 scoped 校验 | 8 |
| `verify_*` | D10 复核·独立重验族 | 已开发 | 独立复现执行者结论 | 5 |
| `build_*` | D10 复核·构建族 | 已开发 | 台账/报表/收据构建 | 7 |
| `fix_*` | D10 复核·修复族 | 已开发 | 缺陷修复脚本 | 5 |
| `scan_*` | D10 复核·扫描族 | 已开发 | 候选缺陷扫描 | 2 |
| `_sync_*` | D10 复核·文档同步族 | 已开发 | 同步入口文档与记忆 | 5 |

### 子目录：`tools/serialize_v4/`（R1 落地的公共骨架，**2026-09-20 新增**）

serialize 族（上方 `serialize_*` 行）的**公共骨架**：1 份模块 + 28 份数据 + 驱动器 + 回归工具。
**28/28 chunk、153 个产物与 `HEAD` 逐字节一致**，旧脚本零改写。
改 serialize 族产物请**优先改数据文件**（`chunks/G01-cNN.json`），不要再复制骨架。

| 文件 | 作用 |
|---|---|
| `serialize_common.py` ✅ | 公共骨架：`put/k/r/x` + check 归属批次（4 模式）+ `serialize()` 驱动 |
| `run_chunk.py` ✅ | 驱动器 CLI（`--chunk N --base <规范根>`；`--dry-run-out` 干跑） |
| `extract_chunk_data.py` ✅ | 从冻结脚本逐字导出数据（铺开其余 chunk 时用） |
| `verify_pilot.py` ✅ | 单 chunk 验收：新产物 vs `HEAD` 逐字节比对 |
| `sweep_family.py` ✅ | 全族回归：28 个 chunk 一次跑完并出报告 |
| `README.md` ✅ | 用法、三条硬约束、每 chunk 参数面（4 条差异轴） |

特殊入口：

| 脚本 | 作用 |
|---|---|
| `review_scoped_validator.py` ✅ | **授权批次 scoped 校验器**（台账引用的正式校验器） |
| `review_full_validator.py` ✅ | 全量校验器（scoped 依赖它的字段定义） |
| `build_acceptance_ledger.py` ✅ | **验收台账构建器**（产出 acceptance-ledger.json） |
| `blocked_authorization.py` ✅ | 受阻批次豁免加载器（授权文件缺失即全禁） |
| `test_review_scoped_validator.py` ✅ | scoped 校验器单元测试（31 项，见下方注意） |
| `_r19_rebuild.py` ✅ | 最近一轮（r19）重建入口 |
| `_r19_validate.py` ✅ | 最近一轮（r19）校验入口 |

### `test_review_scoped_validator.py` —— 曾 2 项 FAIL，**2026-09-19 已修**

> ⚠️ 本节曾长期写着「31 项中 29 PASS / 2 FAIL」，**该断言已过期**（静默过期家族，
> 见技能 `jvs-index-layer-maintenance` 坑 3）。修法与现状如下：

- 原失败用例：`test_payload_cannot_point_to_forbidden_metadata_or_batch`（`batch-170`、`batch-232`）
- 根因：`outputs/full-review-full-20260909/blocked-authorization.json`（2026-09-15 项目方授权）
  把 16 个批次从 BLOCKED 名单豁免，而用例仍按旧预期断言 ⇒ **用例预期随授权文件漂移**
- 修法：注入合成受阻集合 `frozenset({170,171,232})`，用例预期不再依赖授权文件
- **现状以实跑为准**（勿引用任何写死的通过数）：`bash scripts/git-gate.sh` 第 `[8]` 步每次都会实跑该测试，非阻塞但会打印结果

---

## 三、索引与工程脚本（`_migrate/`、`_index/`）

| 脚本 | 作用 |
|---|---|
| `_migrate/classify.py` ✅ | 功能分类规则（改后重跑即刷新索引） |
| `_migrate/make_index_md.py` ✅ | 生成 `_index/功能分类总表.md` |
| `_migrate/make_progress_md.py` ✅ | 生成 `_index/功能进度表.md` + `_handoff/PENDING.md` |
| `_migrate/make_scripts_ledger.py` ✅ | 生成 `_index/一次性脚本登记表.md`（活件 / 一次性 / 可弃） |
| `_migrate/make_scripts_md.py` ✅ | 生成**本文件**（人工登记项在 `CORE` 等列表里） |
| `_migrate/status_map.py` ✅ | 功能进度状态定义（改后重跑刷新进度表） |
| `_migrate/s5_verify.py` ✅ | 迁移完整性校验（junction + SHA + 文件数） |
| `_migrate/build_inventory.py` ✅ | 结构清单扫描 |
| `_handoff/env/check_env.sh` ✅ | **环境自检（接手第一条命令）** |

---

## 四、离线 vs 联网速查

**可以放心直接跑（不联网、不耗配额）：**

- `test_llm_judge.py`、`test_review_scoped_validator.py` —— 回归测试
- `s4_pipeline.py`、`extract_regression.py`、`causal_graph.py` —— 管线与图谱
- `eval_m1.py` —— 规则口径评分
- `name_scan.py` —— 隐私闸门
- `_migrate/*`、`_handoff/env/check_env.sh` —— 工程与自检

**会调模型（先确认配额 + 凭证 + 隐私闸门）：**

- `relay_call.py`、`llm_answer.py`、`llm_judge.py`、`targeted_round.py`
- `genN_bestof.py` —— ⚠️ 给生成与判分子进程传同一份环境，**不用于跨 provider 实验**

---

## 五、动手前必读（按序）

1. `_handoff/HANDOFF.md` —— 接手总览
2. `_handoff/env/ENV.md` —— 环境契约与凭证
3. `_index/功能分类总表.md` —— 东西在哪 + 进度状态
4. `_handoff/PENDING.md` —— 当前待办
5. 本文件 —— 跑哪个脚本

> 路径类文档注意：`03-d10-workspace/.../RUNME-full.md` 的「已知过期」断言（G13 写 r5 实际 r6、G05/G07 版本不符、仍写「复核进行中」）**2026-09-19 核验为不成立** ——
> 该文件已更新至 r19，ledger / package 的 sha256 与磁盘一致（机械核验通过）。
> 若仍有疑问，以 `acceptance-ledger.json` + `bash scripts/git-gate.sh` 的实跑结果为准。
