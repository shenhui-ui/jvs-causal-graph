---
name: jvs-doubao-sub-bridge
description: 启停 JVS 豆包订阅桥（aux_doubao_bridge.py，127.0.0.1:9090，OpenAI 兼容 /v1/chat/completions，走豆包订阅额度）。当需要「后台启动豆包订阅桥」「断掉/关掉豆包桥」「检查桥是否在跑」「doubao_sub / 9090 / 403」时使用。
agent_created: true
---

# JVS 豆包订阅桥启停

把 `DoubaoChatClient`（豆包**订阅额度**）包成 OpenAI 兼容接口，供 `LLM_PROVIDER=doubao_sub` 调用。
用于生成/判边等需要豆包模型、又不想烧方舟 API 额度的场景。

## 环境事实（本机）

| 项 | 值 |
|----|----|
| 桥脚本 | `C:\Users\<user>\Desktop\JVS\01-host-product\2026-08-28-20-59-40\tools\causal\aux_doubao_bridge.py` |
| 会话文件 | 同目录下 `docs\host_memory_dump\.doubao_session.json` |
| 解释器 | `C:\Users\<user>\.workbuddy\binaries\python\envs\default\Scripts\python.exe`（**必须用这个**，`doubao2api` 装在此 env） |
| 端口 | `127.0.0.1:9090`（仅本机；串行锁防风控） |
| 健康端点 | `GET /health` · `GET /v1/models` |
| 调用方配置 | `LLM_PROVIDER=doubao_sub` + `DOUBAO_SUB_KEY=dummy`（**key 随意，桥不校验**） |

> ⚠ **不要用 `LLM_PROVIDER=doubao`** —— 那会打火山方舟官方 API（`ark.cn-beijing.volces.com`，
> 需 `DOUBAO_API_KEY`），订阅场景必然 **403**。区分技巧：桥的 8-token 小请求能过、真请求 403 ⇒ 走错通道。

## ⭐ 生命周期：**用完即关，不需要常开**（2026-09-23 实测补）

**结论：桥是「按需起、用完关」的一次性服务，不常驻。**

| 事实 | 实测 |
|---|---|
| 有没有**自启** | ❌ **没有** —— 全库无任何脚本/计划任务会拉起它（`grep` 9090 无自动化引用）。⚠️ **它跑着 ≠ 它会自启**：看到 `/health` 有响应时，先查「它是不是上一批任务起完忘了关」，**不要**推断「它会自动起」 |
| 有无空闲超时 / 自停机制 | ❌ **没有** —— 源码是裸 `uvicorn.run(app, ...)`（第 146 行），只有 `@app.on_event("shutdown")` 钩子（**收到信号才触发**） |
| 不关会怎样 | **永久驻留**。实测：21:41 起 → 次日 10:53 仍 `LISTENING`，**闲置 13 小时**、计数停在 8 |
| 为何能跨会话存活 | 桥跑在宿主沙箱进程链下（`WorkBuddyAI.exe` → `sandbox-cli.exe` → `bash.exe`×3 → launcher → uvicorn worker）。**该链不随对话/会话结束而终止** ⇒ 会话结束了桥还在 |
| 关闭有代价吗 | **无**。重启成本 = 一条命令 + 数秒（`from_session` 复用本地 `.doubao_session.json`，**不涉及登录/风控**）；累计计数归零属正常 |
| 需常开的理由 | **不存在** —— 已确认无任何自动化/计划任务引用 9090（唯一的自动化是 PAUSED 且用 deepseek，不涉桥） |

⭐ **「无自启 + 无自停」合起来才是完整结论**：桥是**纯被动**的 ——
**只有人显式启动它才存在，只有人显式杀它才消失**。
⚠️ 由此产生两类误判（**都实测踩过**）：
① 把「常在跑」当成「该常开」⇒ 闲置 13 小时；
② 把「恰好在跑」当成「它会自启」（本项目 `MEMORY-detail.md` 曾这样写，2026-09-23 更正）
⇒ **凡描述这类机制，一律给「代码位置」或「反面证据」，不要只写「它（会）怎样」。**

⚠️ **唯一要权衡的是「重启频率」而非「能不能关」**：桥内部复用同一豆包会话（`_cid`，风控友好）。
**高频起停**会让每次首请求都新建会话 ⇒ 更易触发风控。故：
- ✅ **同一批任务内保持常开**（跑一批生成/判分就开一次）；
- ✅ **任务批次结束就关**；
- ❌ 不要「每个请求起一次」。

⚠️ **本技能原缺「何时关」这一节**，只写了怎么起/怎么关 ⇒ 实测踩到「起完忘了关、闲置 13 小时」。
**收尾纪律**：起桥的**同一批工作**在收尾时，必须**同时**执行「断开」节，或明确告知用户「桥仍在跑，建议关闭」。
**判据**：`curl /health` 的 `successful_calls` **长时间不增长** + `netstat` 无 `ESTABLISHED` ⇒ 已闲置，该关。

## 启动（后台）

```bash
cd "C:/Users/<user>/jvs-src/01-host-product/2026-08-28-20-59-40/tools/causal" \
&& "C:/Users/<user>/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -B -u \
   aux_doubao_bridge.py \
   "C:/Users/<user>/jvs-src/01-host-product/2026-08-28-20-59-40/docs/host_memory_dump/.doubao_session.json" \
   9090
```

用 Bash 工具的 `run_in_background: true` 起。**参数顺序**：`[session_json] [port]`（都可省，默认 `.doubao_session.json` + 9090）。

**可选环境变量**：

| 变量 | 含义 |
|---|---|
| `DOUBAO_SUB_THINK` | `0`=快速(默认) `1`=思考 `2`=自动 `3`=专家（豆包2.1 Turbo 专家） |
| `DOUBAO_FRESH` | `1`=每请求新会话（**避开同会话保守拒答**，判分/生成推荐） |
| `DOUBAO_TARGETED_ROUND` | `1`=定向轮（限 8 次上游、强制 think=3 + fresh，异常即停） |

> **`DOUBAO_FRESH=1` 很关键**：桥默认复用同一会话（风控友好），但同会话下豆包容易"保守拒答"；
> 需要完整答案时开 fresh。

## 状态检查

```bash
curl -s --max-time 8 http://127.0.0.1:9090/health
# {"ok":true,"session":".doubao_session.json","think":0,"fresh":false,"targeted":false,
#  "upstream_attempts":24,"successful_calls":24}
```

`successful_calls` / `upstream_attempts` 是**累计计数**，可判断桥是否真在工作。

## 断开（停止）

桥由**两个进程**构成，必须树杀，否则子进程会残留继续占 9090：

```
<launcher python>  ──▶  <uvicorn worker python>   ← 真正 LISTENING 并持有 _client 会话
   (父进程)                (子进程)
```

**步骤**：

1. 找 PID（`tasklist` 可靠；PowerShell 宿主在本环境会**吞掉输出**）：
   ```bash
   netstat -ano | grep ":9090"          # LISTENING 那行的最后一列 = worker PID
   tasklist /FI "IMAGENAME eq python.exe" /FO CSV
   ```
2. 查清命令行确认是桥（避免杀错 python）——用 PowerShell 写文件再读回：
   ```powershell
   $out = Get-CimInstance Win32_Process | Where-Object { $_.Name -like '*python*' } |
          ForEach-Object { "PID={0} PPID={1} CMD={2}" -f $_.ProcessId,$_.ParentProcessId,$_.CommandLine }
   $out | Out-File -FilePath "<tmp>\procs.txt" -Encoding utf8
   ```
   两个 PID 的 `CMD` 都是 `aux_doubao_bridge.py ... 9090` ⇒ 一对父子。
3. **树杀父进程**（连带子进程）：
   ```bash
   taskkill /PID <launcher_pid> /T /F
   ```
4. 验证：
   ```bash
   netstat -ano | grep -E ":9090"   # 只应有 TIME_WAIT/无记录，**不能有 LISTENING**
   curl -s --max-time 5 http://127.0.0.1:9090/health   # 应 “积极拒绝 (os error 10061)”
   ```

> 断前先看 `/health` 与 `netstat` 的**连接数**，确认无活跃消费者再断；
> 若有 `ESTABLISHED` 说明有任务在用，先停任务。

## 已知坑

1. **必须用 workbuddy 的 python env**（`...\workbuddy\binaries\python\envs\default\...`），
   系统 python 没有 `doubao2api` / `fastapi` / `uvicorn`。
2. **`taskkill` 单杀 worker 不够** —— launcher 可能重建或残留；`/T` 树杀父进程更干净。
3. **PowerShell 工具在本环境吞 stdout**：直接 `Get-CimInstance | Format-List` 会「命令成功但无输出」，
   必须 `Out-File` 落盘再 Read。
4. **`from_session` 失败**通常意味着 `.doubao_session.json` 过期，需重新登录
   （同目录 `aux_doubao_login.py`）。
5. 桥**只支持非流式文本对话**，`usage` 字段恒为 0（不返回真实 token 数）。