---
name: diagnose-subagent-failure
description: 诊断 WorkBuddy / host-workspace 无法调用子代理（subagent / Agent 工具 / Task 失败）的根因。当用户反馈"不能调用子代理""子代理报错""子任务失败""Agent tool 用不了"时使用。
agent_created: true
---

# 诊断子代理调用失败

WorkBuddy 系产品里，主代理通过 `Agent` 工具拉起子代理（`subagent_type`: `Explore` / `general-purpose` / `Plan` / `sheet-agent` / `doc-*`）。
子代理失败**极少是工具被禁用**，绝大多数是**子代理那一次模型请求失败**。按下面的顺序查，不要瞎猜。

## 第 0 步：先分清是哪个程序

同一台机器可能装了多个 WorkBuddy 系产品，配置目录不同，别查错对象：

| 程序 | 典型安装路径 | 配置目录 |
| --- | --- | --- |
| WorkBuddy | `D:\LenovoSoftstore\Install\WorkBuddy\WorkBuddy.exe` | `~/.workbuddy` |
| host-workspace | `D:\workbuddy\WorkBuddyAI\WorkBuddyAI.exe` | `~/.workbuddy-ai` |

确认方法：读桌面 `.lnk` 里的路径字符串（COM 读快捷方式可能被安全策略拦，直接 `tr -c '[:print:]' '\n' < x.lnk | grep -i exe` 即可）。

## 第 1 步：直接搜真正的报错（最高效）

```bash
cd ~/.workbuddy/logs/<今天日期>/          # 或 ~/.workbuddy-ai/logs/
grep -rh "Request failed: agent=" *.log | sort | uniq -c | sort -rn
grep -rh "ReasoningEffort invalid" *.log | head
grep -rhiE "sub-agent run errored|propagating as task failure" *.log | head
```

关键模式：`Request failed: agent=<子代理名>` 后面紧跟 `[Interruption] Catch block entered, error: ...`，
再到 `[AgentTask] ... sub-agent run errored — propagating as task failure`。这条链就是子代理失败现场。

同时对比主代理：`Sending request: agent=cli` 的次数 vs `Request failed: agent=cli`。
如果主代理大多成功、子代理 100% 失败 → 是子代理侧特有的参数问题，不是网络/鉴权。

## 第 2 步：确认工具本身是好的（排除误判）

```bash
grep -h "runtime cli agent view ready" *.log | tail -3     # 应输出 "26 agents (used for registration and Agent tool descriptions)"
grep -ho '"--tools","[^"]*"' ~/.workbuddy/logs/daemon.log | grep -c "Agent"   # 启动参数里应含 Agent
```

只要 `Agent` 在 `--tools` 里且 agent view ready，就**不是**工具被禁用，问题在下游模型请求。

## 第 3 步：最常见的根因 —— 自定义模型 + reasoning effort 不合法

自定义模型（`~/.workbuddy/models.json` 里 `vendor: "Custom"`）如果声明了上游不接受的 effort 值，
子代理解析 effort 时会命中那个非法值，请求直接 400。

检查：

```bash
cat ~/.workbuddy/models.json     # 看 reasoning.supportedEfforts
```

**上游 OpenAI 兼容端点的合法值只有：`low` / `medium` / `high` / `xhigh` / `none`。**
`max` 是非法值。

> 注意坑点：`max` 在 WorkBuddy 的**腾讯自家托管模型**上是合法值（例如内置 `glm-5.3` 就声明
> `supportedEfforts: ["low","high","max"]`），所以产品 UI 允许填 `max`，用户很容易照抄。
> 但第三方端点（如 `token.sensenova.cn`）会直接拒绝。判断依据始终是**上游实测**，不是产品里别的模型怎么写。
> 官方内置 `deepseek-v4-flash` 自己只声明 `["high","xhigh"]`，可作参考。

手工复现（最快确认，不依赖日志）：

```bash
KEY=$(python -c "import json;print(json.load(open('models.json'))[0]['apiKey'])")
curl -s -o /dev/null -w "%{http_code}\n" -X POST "<url>/chat/completions" \
  -H "Content-Type: application/json" -H "Authorization: Bearer $KEY" \
  -d '{"model":"<model>","messages":[{"role":"user","content":"hi"}],"max_tokens":8,"reasoning_effort":"max"}'
```

`400` + 响应体 `field ReasoningEffort invalid` 即坐实；换 `high` 应返回 `200`。
（注意：连续快速请求可能返回 `429`，`429` 说明参数合法、只是限流，别和 `400` 混淆。）

修复：删掉非法的 `max`，并显式给一个合法默认值。

```json
"reasoning": {
  "defaultEffort": "high",
  "supportedEfforts": ["low", "medium", "high", "xhigh"]
}
```

改前先备份：`cp models.json models.json.bak-$(date +%Y%m%d)-reasoning`。

**`models.json` 是热加载的，不需要重启应用。** 改完约 1 分钟内在日志里应看到：

```bash
grep -rh "Loaded custom models config from user" <logdir>/*.log | tail -2
```

出现 `(entries=1)` 即已重新读取；再确认运行时快照已更新：

```bash
grep -rhoE ".{120}supportedEfforts\":\[[^]]*\]" <logdir>/*.log | tail -1
```

看到新值（如 `"id":"custom-local:<model>", ... "supportedEfforts":["low","medium","high","xhigh"]`）即已生效。

顺带确认没有别处覆盖 effort（应当查无结果）：

```bash
grep -rlia "reasoningEffort" ~/.workbuddy/local_storage/ ; tr -c '[:print:]' '\n' < ~/.workbuddy/workbuddy.db | grep -c reasoningEffort
```

两者都为 0 → effort 完全由 `models.json` 决定，改它即充分。

修好后把 `supportedEfforts` 里每个值都 curl 一遍，确认没有 400。

## 其他可能根因（前三个都不成立时再看）

1. **模型列表不匹配**：子代理声明的 `models` 是内置模型 id（如 `hy3`/`hy3-x`/`lite`），
   用户只配了自定义模型且产品开关 `UseDefaultModelIfCurrentNotInList=false`，子代理可能解析不到模型。
   查 `grep -h "AgentModelResolver] agent" *.log` 看 `resolved_models` 是否为空。
2. **限额**：`429 rpm exhausted` —— 等一会或提额，不是配置问题。
3. **单个 agent 的 tools 缺依赖**：例如 `ModelBehaviorError: Tool Bash not found in agent contextSummary`
   —— 属于该内置 agent 自身声明问题，与子代理调用无关，别混为一谈。

## 汇报要点

- 明确区分「工具被禁用」和「子代理模型请求失败」——本流程里后者占绝大多数。
- 给出报错原文（含 `agent=` 名称、HTTP 状态码、错误字段）。
- 给出频率分布（哪个 agent、失败几次、从哪天开始），这能直接指向「用户当天改了什么」。
- 说明是否需要重启才能生效。
