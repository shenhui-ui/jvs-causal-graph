# ACP 会话桥 —— 运行说明

`acp_bridge.py` 用**本机 DSH 的 ACP profile**可编程地**新建 / 列举 / 恢复**会话，并投递交接材料。
它是 `project-handoff` **机制层不可用**的替代实现：把「新建接续」从手动改为可编程。

> 依据：勘察报告 `docs/host_memory_dump/M1-10-跨会话寻址工具勘察-20260919.md`（§九 端到端实测）
> 技能：`jvs-acp-session-bridge` ｜ 决策：`_handoff/HANDOFF.md` §10 **R16**（部分满足）

---

## 一、先读这三条（不可省略）

| # | 事实 | 含义 |
|---|---|---|
| 1 | **寻址的是 DSH 会话，不是 WorkBuddy 会话** | 桥只能让 **DSH 侧**接续；WorkBuddy 侧仍靠开场白手动 |
| 2 | **不能强制新会话「先只读核验」** | ACP 无「约定首轮行为」机制。桥把软约束**写进开场白**，但**无法保证对方遵守** |
| 3 | **`session/fork` 不可用** | 实测 `-32601 Method not found`。故**不提供 fork 子命令**；能力声明只有 `{close, list, resume}` |

### ✅ 第四事实（原「git 不可执行」）—— **已定位根因并由桥修复**

**原现象**：接手方在 ACP 会话里跑不了 git —— 裸名报「无法识别为 cmdlet」，
**连绝对路径调 `git.exe` 也无输出**。原判为「硬限制：需落库的改动不能在 ACP 会话内做」。

**⭐ 根因（逐项实测）**：**父进程未设置 `PATHEXT`**，而 **PowerShell 5.1 在该变量缺失时
自身 fallback 到 `.CPL`**（不含 `.EXE`）⇒ PS 不把 `.exe` 当可执行文件。

| 实验 | 结果 |
|---|---|
| 父进程 | `PATHEXT` **未设置** |
| 同一环境交给 `cmd.exe` | 正常长串 ✅ |
| **同一环境交给 PowerShell 5.1** | **`.CPL`** ← PS 自身 fallback |
| 机器级 / 用户级注册表 | 机器级正常；用户级**值不存在** |
| PS + 显式正常 `PATHEXT` | `git version 2.55.0.windows.3` ✅ |

⇒ **与 PowerShell 版本无关，与 git 安装无关**。

⚠️ **次生陷阱**：**绝对路径不能绕过** —— 坏 `PATHEXT` 下
`& '.../git.exe' --version` **输出为空且退出码 0**（静默失败）。「绝对路径兜底」在此**无效且危险**。

**✅ 修复**：桥启动 DSH 时**显式注入正常 `PATHEXT`**（`child_env()`，仅在缺失或缺 `.EXE` 时覆盖），
由 pty 子进程继承。**端到端实测通过**：无任何外部注入时，ACP 会话内
`git --version` = `2.55.0.windows.3`，`git status --porcelain` 能准确报出工作区改动。

⚠️ **仍未复现保护**：**直接手动起 DSH（不经桥）依然会复现**该问题。

⇒ 投递材料**不再需要**写「git 不可用」；但仍**建议**把落库收尾放在 WorkBuddy 侧，便于统一门禁口径。

---

## 二、隐私闸门（强制，fail-closed）

**投递 = 把材料外发给模型。** 按 `README-privacy.md`，**任何上云 payload 必须先过闸门**。

桥的行为：

- 投递前**自动**调用 `tools/host/name_scan.py`；**命中即拒发**（退出码 1）
- **找不到闸门也拒绝**（不做「跳过检查」的降级）
- **显式指定的闸门不存在 ⇒ 报错，不静默回退到默认路径**（避免"以为用了自己的表"的假安全感）
- 材料**全文内联**进 prompt ⇒ **闸门查的字节 = 实际外发的字节**
  （若只给路径，接续方会自行读文件 ⇒ 内容照样进模型上下文却没过闸门 ⇒ 等于绕过）

闸门默认探测顺序：`--name-scan` > `$JVS_NAME_SCAN` > 项目内固定路径 > 同目录 `name_scan.py`。

---

## 三、子命令

| 子命令 | 副作用 | 说明 |
|---|---|---|
| `probe` | **无**（只读） | 握手 + 打印能力声明。**不创建会话、不调模型** |
| `list` | **无**（只读） | 列举会话。不传 `--cwd` = 全量 |
| `new` | **新建 DSH 会话** + 调模型 | 新建并投递交接材料 |
| `resume` | 调模型 | 恢复到已有会话并投递（会话需**非活跃**，否则报错） |

### 常用参数

| 参数 | 说明 |
|---|---|
| `--material <f>` | 交接材料，**可多次**。全文内联进 prompt 并过闸门 |
| `--prompt-file <f>` | 附加说明文件（可选），同样内联并过闸门 |
| `--extra "<text>"` | 追加到开场白末尾的指令（如本轮特别要求） |
| `--cwd <dir>` | 会话工作目录（默认当前目录） |
| `--dry-run` | **只打印、不创建、不调模型**（推荐先跑） |
| `--print-reply` | 打印接续会话的回复全文 |
| `--close` | 投递后关闭会话 |
| `--json` | 机读输出（可前置或后置） |
| `--max-bytes N` | 单份材料内联上限（默认 200,000 B，超出截断并标注） |
| `--timeout` / `--prompt-timeout` | 握手/管理超时（默认 120s） / 模型推理超时（默认 600s） |

### 退出码

`0`=成功 ｜ `1`=闸门拦截 ｜ `2`=环境/协议失败 ｜ `3`=参数错误

---

## 四、标准用法

```bash
PY="C:/Users/<user>/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe"
B="01-host-product/2026-08-28-20-59-40/tools/host/acp_bridge.py"

# 1) 先只读探环境（零副作用）
"$PY" "$B" probe
"$PY" "$B" list --cwd "C:/Users/<user>/jvs-src" --limit 10

# 2) 干跑核对将要外发的全文（零副作用，强烈建议）
"$PY" "$B" new --material "_handoff/HANDOFF.md" --extra "只读核验" --dry-run

# 3) 真投递（会新建会话并调模型）
"$PY" "$B" new --material "_handoff/HANDOFF.md" --print-reply

# 4) 续投（用返回的 sessionId）
"$PY" "$B" resume --session-id <id> --material <新材料> --print-reply
```

---

## 五、实测结论（2026-09-19，均为原样输出）

| 项 | 结果 |
|---|---|
| `probe` | `protocolVersion: 1`；agent = `deepseek-harness-acp`；`authMethods: []`（**免凭证**） |
| 能力声明 | `{close, list, resume}` —— **不含 fork** |
| `session/new` | 返回真实 sessionId |
| `session/prompt` | `stopReason: end_turn` + 收到 `agent_message_chunk` |
| **软约束实效** | 接续会话**真的**按开场白读了材料并回报 ①②③ |
| **`session/resume`** | ✅ 可用；**且保留历史上下文**（实测：不给口令仍能复述上一轮的随机口令） |
| `session/fork` | ❌ `-32601` |
| 闸门拦截 | ✅ 含真名材料被拒（退出码 1） |
| 闸门 fail-closed | ✅ 显式指定不存在的闸门 ⇒ 拒发 |
| 落盘 | `~/.dsh/sessions/<workspace-key>/<uuid>/session.v3.jsonl.zstd` |

> 注：报告 §七 原写「resume 不重放历史」—— 实测**与之相反**，已更正。

---

## 六、已知限制

1. ~~**git 不可执行**~~ → ✅ **已修复**（见 §一 第四事实）：桥注入 `PATHEXT` 后 git 可用。
   ⚠️ 但**手动起 DSH 仍会复现**
2. **`session/list` 不是会话全集** —— 磁盘目录数 > 列表数（实测 68 vs 33），
   与 `archivedSessionIds` 归档有关，**精确规则未定**
3. **sessionId 有两种格式** —— 裸 UUID 与 `session-` 前缀（旧会话）
4. **`session/resume` 不能对活跃会话调用** ⇒ 报「session 已活跃」；先 `--close` 再 resume
5. **ACP v1 稳定、v2 未稳定**（`schema/v2/schema.unstable.json` 存在）⇒ **只用 v1**
6. **权限边界未评估** —— 桥以 `--cwd` 指定的目录作为会话工作目录，其权限范围未与 WorkBuddy 侧对齐
7. **DSH 版本迭代极快** ⇒ 桥按「安装序号最大者」自动探测，不写死版本号；升级后无需改脚本

---

## 七、环境修复：`PATHEXT`（2026-09-19）

### 症状与误判路径

接手方报告「ACP 会话里 git 用不了」。**两个诱人的错误结论**：① 以为 PowerShell 5.1 太旧；
② 以为要用绝对路径兜底。**两条都错**。

### 根因

**父进程未设置 `PATHEXT`** ⇒ **PowerShell 5.1 自身 fallback 到 `.CPL`**（不含 `.EXE`）
⇒ PS 不把 `.exe` 当可执行文件。判别依据：

```bash
# 同一环境，交给 cmd 与交给 PS，结果不同 ⇒ 说明是 PS 侧 fallback，不是环境被污染
python -c "
import subprocess,os
e=dict(os.environ); e.pop('PATHEXT',None)
print('cmd :', subprocess.run(['cmd','/c','echo','%PATHEXT%'],capture_output=True,text=True,env=e).stdout.strip())
"
# PowerShell 侧对比见本仓库报告 §9.6.4 的六行实测表
```

### 修复

`acp_bridge.py` 的 `child_env()`：启动 DSH 时**显式注入** `PATHEXT_DEFAULT`，
**仅在缺失或缺 `.EXE` 时**覆盖（避免掩盖真实配置）。

### 验证方法

```bash
python tools/host/acp_bridge.py probe          # 看「本进程 / 子进程 PATHEXT」两行
python tools/host/acp_bridge.py new \
  --material <一份要求回报 git --version 的材料> --cwd <repo> --print-reply --close
```

### ⚠️ 三条遗留

1. **绝对路径不是解法** —— 坏 `PATHEXT` 下绝对路径调 `.exe` **静默失败**（空输出、退出码 0）
2. **手动起 DSH 仍复现** —— 修复只在「经桥启动」的路径上生效
3. **升级 shell 不是解法** —— 见下节实测

### ❌ 已排除的解法：升级到 PowerShell 7（2026-09-19 实测）

「换成 PS7 会不会好」是个自然的猜想，**实测证否**：

| 实测项 | PS 5.1 | PS **7.6.6**（官方便携版实测） |
|---|---|---|
| `PATHEXT` 缺失时 | fallback 到 **`.CPL`** | **同样 fallback 到 `.CPL`**（逐字节一致） |
| `git --version` | 报「无法识别」 | **同样**报「无法识别」 |
| 绝对路径调 `.exe` | 静默失败（空输出、rc=0） | **同样**静默失败 |
| `Start-Process` 兜底 | 可用 | 可用 |
| 启动耗时（相对） | 基线 | **慢约 37%** |
| `$OutputEncoding` | `us-ascii` | `utf-8` ← **唯一真实差异** |

结论：**`.CPL` 不是 PS 5.1 的版本缺陷，是「该变量缺失时的兜底行为」，PS7 一模一样。**
⇒ **升级与本议题无关**；且 DSH 已内置对策覆盖了编码差异（`ENCODING_PREAMBLE`，见报告 §9.6.6）。

> 附带确认（不影响结论）：DSH 的 shell 解析顺序是 **PS7 安装位置 → PATH → PS 5.1 兜底**
> ⇒ 装了 PS7 **会被采用**（已调官方解析函数验证）。但因为 PS7 行为相同，采用也无改善。

### 为什么会踩

`git.exe` 在 PATH 里、`.git` 目录也在、`git --version` 却报「无法识别」时，
**人最自然的反应是怀疑 shell 或 git 安装**。真正该查的是**可执行扩展名解析**这一层。

---

## 七、本桥不做什么

- **不**推送、**不**安装、**不**改配置、**不**删除任何文件
- **不**代替人工签字、**不**作语义裁决
- **不**承诺自动新建接续（那是**触发**问题，与「能否寻址」是**两个独立缺口**）

唯一副作用：DSH 侧**新建会话**（`new`）或**投递内容**（`resume`）。