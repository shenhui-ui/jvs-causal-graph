# 环境契约 —— JVS / M1 跨会话因果问答

> 截至 2026-09-16 实测 ｜ 自检命令：`bash _handoff/env/check_env.sh`

---

## 1. 解释器

| 用途 | 路径 / 值 |
|---|---|
| **推荐（托管）** | `C:\Users\<user>\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe` |
| 实测版本 | `Python 3.13.14` |
| 系统回退 | 3.12.14（`C:\Users\<user>\.venv-html-to-docx\Scripts\python.exe`） |

脚本一律**加 `-B`** 运行，避免写 `__pycache__` 污染目录：

```bash
PY="C:/Users/<user>/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe"
"$PY" -B <script>.py
```

### 1.1 可执行入口的解释器可覆盖（`JVS_PY`）

**4 个可执行入口**的 `PY` 定义行写成 `PY="${JVS_PY:-<上表托管路径>}"`：

| 入口 | 位置 |
|---|---|
| 合入门禁 | `scripts/git-gate.sh` |
| 凭证扫描 | `scripts/scan-secrets.sh` |
| 环境自检 | `_handoff/env/check_env.sh` |
| pre-commit 钩子 | `.githooks/pre-commit` |

- **不设 `JVS_PY`** ⇒ 行为与改造前**逐字节相同**（换机器前无需任何操作）。
- **设了 `JVS_PY`** ⇒ 4 个入口一律走该解释器，用于换机 / 换运行时。
- ⚠️ **不做自动探测**（不扫 PATH、不试多个候选）：路径**确定性**优先。
  这不是「容错」特性 —— 设了错的 `JVS_PY` **应当失败**：
  `git-gate` 第 `[1]` 步会判 `FAIL` 并**指名**该路径（fail-closed，与 `[5]` / `[10]` 同构）。
- ⚠️ **只覆盖「可执行入口」，不覆盖文档里的示例片段** ——
  `ENV.md` / `RUN-CONTRACT.md` / `_migrate/make_scripts_ledger.py` 的产物内示例
  仍写上表字面路径：它们是**给人看的**，不是执行入口。

```bash
# 换机示例：指向另一套 Python
export JVS_PY="/usr/local/bin/python3"
bash scripts/git-gate.sh
```

## 2. 模型凭证（**不落盘**）

密钥**只走环境变量**，绝不写进任何文件、命令、日志或报告。

| 变量 | 用途 |
|---|---|
| `RELAY_KEY` | 主凭证（`relay_call.py` 首选） |
| `PROBE_KEY` | 备用凭证（`relay_call.py` 次选） |

```bash
export RELAY_KEY=***          # 具体值问项目负责人，勿提交
```

`relay_call.py` 未取到 key 会直接 `RuntimeError: RELAY_KEY/PROBE_KEY 未设置` 中止 —— 这是**设计如此**，不是 bug。

> ⚠️ **安全历史**：历史文档提到曾有 API key 出现在聊天记录中，已建议轮换。
> 接手时请确认现用 key 是否为轮换后的。

## 3. LLM 通道

| 项 | 值 | 来源 |
|---|---|---|
| 主通道 | **SenseNova 中转** `https://token.sensenova.cn/v1` | `M1-交接包-20260830.md` |
| 默认模型 | `glm-5.2`（`reasoning_effort=low`） | `relay_call.py` 默认值 |
| 判分锁定 | `sensenova` / `deepseek-v4-pro` | `README-run.md` §5 |
| 生成锁定 | `doubao_sub` / `doubao` | `README-run.md` §5 |
| 调用器 | `tools/causal/relay_call.py` | — |

**已知不可用**：`deepseek-v4-flash` 中转侧 **TPM 已耗尽**，勿用
（`M1-交接包-20260830.md` 明载）。

其他被脚本引用的端点（备用/历史）：
`ark.cn-beijing.volces.com`（豆包）、`open.bigmodel.cn`（GLM）、
`api.siliconflow.cn`、`openrouter.ai`、`dashscope.aliyuncs.com`。

## 4. 脚本环境变量全表

| 变量 | 默认 | 作用 |
|---|---|---|
| `LLM_PROVIDER` | `doubao` | 判分 provider |
| `LLM_MODEL` | `""` | 判分模型 |
| `LLM_JUDGE_MAX_TOKENS` | `4096` | 判分最大 token |
| `LLM_GAP` | `16` | 重试间隔秒 |
| `LLM_JUDGE_CAUSE_RELAX` | `0` | cause 判分放宽开关 |
| `LLM_PROMPT_EVIDENCE_FIRST` | 关闭 | 三行约束 + 已召回事件编号对照 |
| `PYTHONDONTWRITEBYTECODE` | — | 建议 `1` |
| `PYTHONIOENCODING` | — | 建议 `utf-8`（Windows 控制台） |
| `DOUBAO_TARGETED_ROUND` | — | 定向实验隔离桥开关 |
| `DOUBAO_SUB_THINK` | — | 定向实验 `3` |
| `DOUBAO_FRESH` | — | 定向实验 `1` |

## 5. 外部工具依赖（实测）

| 工具 | 实测值 | 状态 |
|---|---|---|
| Python | 3.13.14 | ✅ |
| Node.js | **v22.22.2**（托管） | ⚠️ 见下 |
| git | 2.55.0.windows.3 | ✅ |
| OpenClaw | 全局 npm 安装 | ⚠️ **版本冲突** |

### ⚠️ 两个已知环境风险

**① OpenClaw 与当前托管 node 不兼容**

实测输出：
```
openclaw: Node.js >=22.22.3 <23, >=24.15.0 <25, or >=25.9.0 is required
          (current: v22.22.2)
```

托管 node `v22.22.2` **低于** openclaw 要求的 `>=22.22.3`。
- 历史安装用的是**系统 node v24.19.0**（`C:\Program Files\nodejs`）
- 若需 openclaw，请把 PATH 指向系统 node v24
- **但注意**：W3 已实测 **gateway 服务未安装时裸跑 embedded fallback 可正常工作**，
  多数场景不依赖 openclaw
- ✅ **2026-09-20 已装 gateway 常驻服务**（计划任务 `OpenClaw Gateway`，运行时用系统 node v24，
  probe ok、端口 18789 HTTP 200）。前置：需在「安全中心 → 命令安全 → 程序黑名单」放开
  `schtasks.exe`。详见 `01-host-product/2026-08-28-20-59-40/docs/环境安装记录-20260829.md` §六

**② 历史安装踩坑（务必规避）**

- `npm install -g openclaw` 会被 npm 的 allow-scripts 拦截 postinstall
  → 必须 `npm install -g --allow-scripts=openclaw,...` 重跑一次，否则插件不全
- 插件注册表可能不全：`openclaw plugins registry --refresh` 重建
- 早期 WorkBuddy 自管 node 目录里的 openclaw 副本是**损坏的**，已备份为
  `~\bin\openclaw.workbuddy-bak`

## 6. 运行铁律（血泪教训）

来自 `M1-W3-进度-20260831.md` 运行备忘：

1. **所有外部调用必须加外层超时**：`timeout -k 15 600`
   —— openclaw 失败会内部长时间重试，曾挂 15.7 小时才放弃
2. **mid-stream 超时（~107s）偶发**，重试即可过
3. **DNS `ENOTFOUND`** 多为机器休眠/网络瞬断；重试前先探活：
   `curl -sI https://api.deepseek.com`（返回 401 即为通）
4. 批结果解析 `_load_batch_result` 先 JSONL 后 `extract_array` 兜底，
   回包含日志行时打 WARN 属**预期噪音**，可忽略

## 7. 隐私闸门（强制）

`tools/host/name_scan.py` 是外发前闸门。W3 实测**按设计拦下 10 个批次**（命中即拒发，零外发）。

- **默认表**（内置 66 人 + 别名）是运作口径
- `--names identities-init.json` 严格表会**误报 Z 码本身**（身份表 aliases 含 Z 码被编入正则）
  → 已知工具小缺陷，勿用严格表
- **任何外发前必须过闸门**；命中即停，不自动外发、不自动改写 gold

另见 `tools/host/README-privacy.md`。

## 8. 隔离纪律

- 真实调用前确认 **provider / 凭证 / 配额 / 隐私闸门**
- 输出写入**独立目录**，**不覆盖冻结实验结果**
- 冻结路径（`m1_10_baseline_final` 等）重跑请用**新输出路径**，不要直接覆盖
- 受限素材（`04-restricted-materials/`）分析必须本机完成，**不得外发**