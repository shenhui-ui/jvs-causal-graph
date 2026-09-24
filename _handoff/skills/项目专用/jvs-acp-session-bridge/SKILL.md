---
name: jvs-acp-session-bridge
description: 用本机 DSH 的 ACP profile（dsh --profile acp）可编程地创建、投递、列举、恢复会话，用于 JVS 的跨会话交接。当需要「新建会话并投递交接材料」「程序化唤起 DSH 会话」「session/new / session/prompt / session/list」「方案 D / ACP 桥」「跨会话寻址」时使用。
agent_created: true
---

# JVS ACP 会话桥（dsh --profile acp）

## 这个技能解决什么

JVS 原先缺「跨会话寻址工具」（Codex 的 `create_thread` / `send_message_to_thread` 类），
据此把上游 `project-handoff` 的机制层整条否决（`HANDOFF.md` §10 **R16**）。

**2026-09-19 实测：本机 DSH 的 `--profile acp` 提供等价能力，且已端到端打通。**
本技能记录**怎么用**与**哪些坑**。

> 报告：`01-host-product/<会话>/docs/host_memory_dump/M1-10-跨会话寻址工具勘察-20260919.md`（§九 = 实测证据）

---

## 环境事实（引用前现测，勿照抄）

| 项 | 值 | 现测命令 |
|---|---|---|
| DSH 入口 | `dsh-install-*/node_modules/@deepseek-ai/dsh/lib/bin.js` | `ls -d ~/.workbuddy-ai/binaries/node/dsh-install-*` |
| node | 用**管理版** `~/.workbuddy-ai/binaries/node/versions/<ver>/node.exe` | 见 `versions/current` |
| ACP SDK | `~/.dsh/profiles/node_modules/@agentclientprotocol/sdk`（Apache-2.0） | `ls -1 <该目录>` |
| 会话落盘 | `~/.dsh/sessions/<workspace-key>/<uuid>/` | `ls -1 ~/.dsh/sessions` |

⚠️ **版本迭代极快** —— 用前查 `npm view @deepseek-ai/dsh dist-tags`。

---

## ⚠️ 三个必知事实（不知会误判）

1. **`~/.dsh/profiles/` 下没有 `acp` 目录**（只有 `headless`、`web`），但
   **`dsh --profile acp` 实测可用** ⇒ profile 从**安装包 bundle**（`@deepseek-ai/dsh-acp-app`）解析。
   **不要**用「profiles/ 下没有 ⇒ 不可用」来判断。
2. **落盘文件名是 `session.v3.jsonl.zstd`**（不是 `session.jsonl.zstd`）；
   且**新旧两格式并存**，部分会话两者都有。新建会话落 `.v3.`。
3. **`session/list` 不返回全部磁盘会话**（实测磁盘 68 目录 vs list 33 条）；
   `workspace.json` 的 `archivedSessionIds` 是**部分**原因，精确规则未定 ⇒ **勿把 list 当全集**。

---

## 握手流程（照做）

```js
// 1) 启动：stdout 只保留换行分隔的 ACP JSON-RPC frame，stderr 是日志
const child = spawn(NODE, [DSH_BIN, '--profile', 'acp'], {
  cwd: 'C:/Users/<user>/.dsh',      // 用 DSH_HOME 作 cwd
  stdio: ['pipe', 'pipe', 'pipe'],
});

// 2) 逐行解析 stdout（必须按 \n 切，不能假定一次 data 一条）
child.stdout.setEncoding('utf8');
let rx = '';
child.stdout.on('data', (c) => {
  rx += c;
  let i;
  while ((i = rx.indexOf('\n')) >= 0) {
    const line = rx.slice(0, i).trim(); rx = rx.slice(i + 1);
    if (!line) continue;
    const msg = JSON.parse(line);
    // msg.id 有值 = 响应；msg.method 有值 = 通知（如 session/update）
  }
});

// 3) 发请求
function rpc(method, params, timeoutMs = 180000) {
  idc += 1; const id = idc;
  child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
  return new Promise((res, rej) => {
    const t = setTimeout(() => rej(new Error('timeout: ' + method)), timeoutMs);
    pending.set(id, (m) => { clearTimeout(t); res(m); });
  });
}

// 4) 标准顺序：initialize → session/new → session/prompt → session/close
```

### 方法签名（**自 schema 读，勿凭记忆**）

```bash
S=~/.dsh/profiles/node_modules/@agentclientprotocol/sdk
python -c "
import json; d=json.load(open(r'$S/schema/schema.json',encoding='utf-8')); defs=d['\$defs']
for n in ['InitializeRequest','NewSessionRequest','PromptRequest','ListSessionsRequest','ResumeSessionRequest']:
    o=defs[n]; print(n, list((o.get('properties') or {}).keys()), o.get('required'))
"
```

| 方法 | 必需参数 | 返回 |
|---|---|---|
| `initialize` | `protocolVersion`（填 `1`） | `protocolVersion` / `agentInfo` / `agentCapabilities` / `authMethods` |
| `session/new` | `cwd`, `mcpServers`（可 `[]`） | `sessionId`（**裸 UUID**） |
| `session/prompt` | `sessionId`, `prompt`（数组，元素 `{type:'text',text}`） | `stopReason`（如 `end_turn`） |
| `session/list` | 无（可选 `cwd` 过滤） | `sessions[]`（字段仅 `sessionId` / `cwd`） |
| `session/resume` | `sessionId`, `cwd` | ⚠️ **不重放历史** |

### 收模型回复

文本从**通知**里来，不是从 `prompt` 的返回里：

```
method: "session/update"
params.update.sessionUpdate === "agent_message_chunk"
params.update.content.type === "text"  →  .text 累加
```

---

## 实测结论（2026-09-19）

### 底座（探针，十项全绿）

`--help` ✅ ｜ `initialize` ✅ ｜ 能力声明 `{close,list,resume}` ✅ ｜ `session/new` ✅ ｜
`session/prompt` ✅ ｜ **模型真实回复「ACP 桥已验证。」** ✅ ｜ `session/update` ✅ ｜
`session/list`（含 cwd 过滤）✅ ｜ 落盘 ✅ ｜ `session/close` ✅

**免凭证**：`initialize` 返回 `authMethods: []`。

### 成品桥（`acp_bridge.py`）

| 项 | 结果 |
|---|---|
| `probe` | ✅ 能力 = `{close, list, resume}`；`forkSupported: false` |
| `list` | ✅ 全量 35 条；`--cwd` 过滤正确 |
| `new` + 投递 | ✅ 真实 sessionId；`end_turn`；11.4s |
| **软约束实效** | ✅ 接续方**真的**按开场白先读材料并回报 ①②③ |
| `resume` 续投 | ✅ 3.7s |
| **`resume` 保留历史** | ✅ 见下 |
| 闸门拦截 | ✅ 含真名材料 → 退出码 1 |
| 闸门 fail-closed | ✅ 显式指定不存在的闸门 → 退出码 1 |
| `session/fork` | ❌ `-32601` |

### ⭐ `session/resume` **保留对话历史**（决定性实验）

方法：新建会话 → 告知随机口令「蓝鲸-7419」→ 关闭 → `resume` → **不下发口令**，直接问「上文口令是什么」。

结果：模型**准确复述「蓝鲸-7419」** ⇒ **对话历史被保留**。
⚠️ 原报告写「不重放历史」**是误读**（README 原句语境是**不重放 MCP 声明**）。
**但仍不应依赖它做交接** —— 无文档承诺、跨版本可能变 ⇒ **材料仍须自包含**。

---

## ⚠️ 四条硬限制（不得省略，否则误导接手者）

1. **寻址的是 DSH 会话，不是 WorkBuddy 会话** —— 两者是不同产品。
2. **不能强制新会话「先只读核验」** —— ACP 无「约定首轮行为」机制，
   上游自动接续第 3 步只能靠**开场白文本软约束**（实测**真的生效**，但**无强制力**；
   6 步中 2 步完整、4 步部分）。
3. **`session/fork` 实测 `-32601 Method not found`，确证不可用** ——
   能力声明只有 `{close, list, resume}`。⇒ 分叉映射**已下调**，桥**不提供 fork 子命令**。
   ⇒ 教训：**读 schema 只能确认方法存在，不能确认被实现**。
   同期**未测**：带 MCP 的会话、权限边界。
4. **无 GUI 打开、无跨位置移交**（上游有 `navigate_to_codex_page` / `handoff_thread`，无对应物）。
5. ~~**ACP 会话内 git 不可执行**~~ → ✅ **2026-09-19 同日已定位根因并由桥修复**（**反面教材，勿再照抄旧结论**）——
   原现象：该会话 shell 是 **PowerShell 5.1**，其 **`PATHEXT` = `.CPL`（不含 `.EXE`）**
   ⇒ `git` / `git.exe` / **绝对路径全部无输出**。
   **⭐ 根因**：**父进程未设 `PATHEXT`** ⇒ **PS 5.1 自身 fallback 到 `.CPL`**。
   判别依据：**同一环境**交给 `cmd.exe` 得正常长串、交给 PS 5.1 得 `.CPL`
   ⇒ 是 PS 侧 fallback，**不是环境被污染，也与 PS 版本无关**（此条**已实测证否**，非推论，见下）。
   **✅ 修法**：桥启动 DSH 时经 `child_env()` **显式注入正常 `PATHEXT`**（仅在缺失或缺 `.EXE` 时覆盖），
   由 pty 子进程继承。**实测**：无任何外部注入时，会话内 `git --version` = `2.55.0.windows.3`、
   `git status --porcelain` 能准确报出工作区改动。
   ⚠️ **三条遗留**：① **绝对路径不是解法** —— 坏 `PATHEXT` 下 `& '.../git.exe'` **静默失败**（空输出、退出码 0），
   「绝对路径兜底」在此**无效且危险**；② **手动起 DSH（不经桥）仍复现**；
   ③ **升级 shell 不是解法**（见下）。
   ⇒ 投递材料**不再需要**写「git 不可用」；落库收尾仍**建议**回侧做（便于统一门禁口径），但**不再是硬限制**。

   **❌ 已实测排除：升级到 PowerShell 7 无效**（2026-09-19，下载官方便携版实测，未安装、未改 PATH、未动注册表）：

   | 项 | PS 5.1 | PS 7.6.6 |
   |---|---|---|
   | `PATHEXT` 缺失时 | `.CPL` | **同样 `.CPL`**（逐字节一致） |
   | `git --version` | 无法识别 | **同样**无法识别 |
   | 绝对路径调 `.exe` | 静默失败 | **同样**静默失败 |
   | 启动耗时 | 基线 | **慢约 37%** |
   | `$OutputEncoding` | `us-ascii` | `utf-8` ← **唯一真实差异** |

   ⇒ **`.CPL` 是「变量缺失时的兜底行为」，不是版本缺陷** ⇒ **别为这事升 PS7**。
   附带确认：DSH 的解析顺序是 **PS7 安装位置 → PATH → PS 5.1 兜底**（已调官方解析函数验证），
   **装了会被采用**，但因行为相同而**无改善**；编码差异 DSH 已用 `ENCODING_PREAMBLE` 无条件覆盖。
6. **`session/resume` 不能对活跃会话调用**（报「session 已活跃」）⇒ 先 `--close` 再 resume。

---

## ⭐⭐ 「底座通」≠「产品通」（最容易说错的一句）

| 层 | 状态 |
|---|---|
| 底座（DSH ACP） | ✅ 已实测打通 |
| 桥（成品代码） | ✅ **已交付** —— `tools/host/acp_bridge.py`（零依赖）+ `README-acp-bridge.md` |
| 接线（交接流程） | ⚠️ **半通** —— 能投递并收到回复；但**未与 §12 登记表自动联动**（登记行仍靠人写） |
| 触发（何时提醒） | ❌ 无解 —— 缺压缩事件/计数器（**与本议题无关，别混**） |

⇒ 被问「打通了吗」**必须分层回答**；`status_map` 中方案 D 记「**开发中**」。

---

## 成品桥（2026-09-19 交付）

**入口**：`01-host-product/2026-08-28-20-59-40/tools/host/acp_bridge.py`
**说明**：同目录 `README-acp-bridge.md`（含完整参数表与实测证据）

```bash
PY="C:/Users/<user>/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe"
B="01-host-product/2026-08-28-20-59-40/tools/host/acp_bridge.py"

"$PY" "$B" probe                                        # 只读探环境（零副作用）
"$PY" "$B" list --cwd <dir> --limit 10                  # 只读列举
"$PY" "$B" new --material <f> --dry-run                 # 干跑（核对将外发的全文）
"$PY" "$B" new --material <f> --print-reply             # 真投递（调模型）
"$PY" "$B" resume --session-id <id> --material <f>      # 续投
```

| 子命令 | 副作用 |
|---|---|
| `probe` / `list` | **无**（只读，不调模型） |
| `new` / `resume` | **新建/投递 DSH 会话 + 调模型** |

**退出码**：`0`=成功 ｜ `1`=闸门拦截 ｜ `2`=环境/协议失败 ｜ `3`=参数错误

### ⭐ 桥内建隐私闸门（强制，fail-closed）

投递 = **外发**。按 `tools/host/README-privacy.md`，**任何上云 payload 必须先过闸门**：

- 投递前**自动**调用 `name_scan.py`；**命中即拒发**（退出码 1）
- **找不到闸门也拒绝**；**显式指定的闸门不存在 ⇒ 报错，不静默回退**
- 材料**全文内联**进 prompt ⇒ **闸门查的字节 = 实际外发的字节**

> ⭐⭐ **开发期发现的真缺陷（两条，均已修）**：
> ① 初版只把材料**路径**写进 prompt ⇒ 闸门查了个**空壳**，含真名材料**照样放行**；
> 更隐蔽的是接续方会**自行读那些路径** ⇒ 内容照样进上下文但**没过闸门** ⇒ **等于绕过**。
> ② `--name-scan <不存在>` **静默回退**默认路径 ⇒ 假安全感。
> ⇒ 教训：**闸门类校验必须验证「查的对象 == 实发的对象」**，否则得到「永远放行」的假闸门。

---

## 纪律与边界

- ✅ **零安装** —— DSH 与 SDK 均已就位；**不安装、不改配置、不推送**
- ⚠️ **`new` / `resume` 会真的创建会话并调用模型**（消耗额度）。
  只读验证用 `probe`（或用 `--dry-run` 干跑核对将外发的全文），
  **不要**用 `session/new` 当「连通性测试」
- ⚠️ **投递必过隐私闸门** —— 桥已内建（fail-closed），**不要**想办法绕过；
  含真名材料须先脱敏（`tools/causal/desensitize_feishu.py`）
- 📁 **探针/临时脚本放工作区外**（如 `C:\Users\<user>\_staging\`）；
  **成品桥进仓** `tools/host/`
- ⚠️ 做**自动触发**仍需先修订 `HANDOFF.md` §11「不添加定时任务」；
  但**「用户主动要求 → 程序代为创建并投递」不需要定时任务**，**已可直接落地**
- ⚠️ `session/list` 计数口径未定 ⇒ 报告与状态文件里**不要写死会话数**

---

## 踩过的坑（实测）

1. **入口路径写错**：`bin/dsh.js` ✗ —— `package.json` 的 `bin` 是 **`lib/bin.js`**。
   查法：`python -c "import json;print(json.load(open('.../dsh/package.json'))['bin'])"`
2. **检查串含反引号会炸 shell**：`grep -cF "...\`session/fork\`..."` 在 bash 里报
   `unexpected EOF while looking for matching backtick`，整条命令不执行。
   ⇒ 含反引号时改用 Python 比对，或换成不含反引号的片段。
3. **`argparse` 子解析器会覆盖主解析器的值**：初版 `--json` 前置时不生效
   （子解析器默认值 `False` 覆盖了主解析器设的 `True`）。
   ⇒ 全局参数若想**前置后置都能用**，`default` 必须用 **`argparse.SUPPRESS`**，
   并在 `main()` 里 `getattr` 兜底默认值。
4. **别用 `--prompt-file` 只传路径** —— 见上文「隐私闸门」：接续方会自行读文件，
   内容照样进上下文却**没过闸门** ⇒ 必须**全文内联**。
5. **`name_scan.py` 自探失败**：把桥从工作区外拷进仓后，闸门探测路径
   需同时覆盖「项目内固定路径」与「同目录」；只写死一处会导致换位置就 fail-closed 拒发。
6. **stdout 分帧**：必须按 `\n` 切，一次 `data` 可能含多条 frame 或半条。
7. **`session/list` 的 cwd**：正斜杠 `C:/...` 与反斜杠 `D:\...` 都能正确匹配，
   但**返回的 cwd 原样保留写入时的格式** ⇒ 逐字符比对会失败，需归一化。
8. ⭐ **闸门查「空壳」= 假闸门**（本轮最贵的开发缺陷，详见上文「隐私闸门」）：
   只把材料**路径**写进 prompt ⇒ 闸门扫的是不含正文的 prompt ⇒ 含真名照样放行；
   而接续方会照路径**自行读文件** ⇒ 内容照样进模型上下文、**从未过闸门** = 绕过闸门。
   ⇒ **闸门必须验证「查的对象 == 实发的对象」**（全文内联，闸门查的字节 = 外发的字节）。
9. **显式指定的闸门不存在却静默回退** ⇒ fail-open（假安全感）。
   ⇒ **显式声明就必须生效**：指定了不存在的 `--name-scan` 时报错退出，不回落默认路径。
10. **`resume` 前必须 `close`**：对**活跃**会话 `resume` 会报
    「session 已活跃」；先 `session/close` 再 `resume` 即成功（且**保留对话历史**）。
11. **提交长信息不要用 heredoc**（本项目环境）：会被安全检查拦截导致 commit 失败。
    ⇒ 一律写**消息文件**再用 `git commit -F <file>`。

---

## 快查清单

- [ ] 只读验证用 `probe`（**零副作用**）；`new`/`resume` **会调模型**
- [ ] 真投递前先 `--dry-run` 核对将外发的全文
- [ ] 投递**必过隐私闸门**（桥已内建 fail-closed，别绕）
- [ ] 先 `initialize`，确认 `authMethods: []` 与 `sessionCapabilities`
- [ ] `session/new` 传**绝对** `cwd`，`mcpServers: []` 起步
- [ ] 文本回复从 `session/update` 的 `agent_message_chunk` 收
- [ ] 结束时 `session/close`；`resume` 前**必须**先 close（活跃会话会报错）
- [ ] 报告/文档里**分级写**：底座 / 桥 / 接线 / 触发
- [ ] 别把 `session/list` 当会话全集；别写死会话数
- [ ] **别提供 fork**（实测 `-32601`）
- [ ] ~~投递材料须写明「ACP 会话内 git 不可用」~~ → **已修复，无需再写**（见硬限制 §5）
- [ ] 排查「会话内某命令用不了」：**先看 `PATHEXT`**，别先怪版本/安装
- [ ] 想「升级 shell/工具解决」→ 先做**便携版离线对照实测**（PS 7.6.6 实测无改善）
- [ ] 桥启动时会自动注入正常 `PATHEXT`；`probe` 会打印「本进程 / 子进程」两行供核对

---

## 附录：环境类断言的排查方法（本轮的通用教训）

> 由 `PATHEXT` 一役提炼 —— 「**某个命令在某个 shell 里用不了**」这类问题，
> **最容易被误判为「版本旧」或「没装」**。

**四条原则**

1. **先分层定位，不要直接下结论**
   命令 → shell（`cmd` / PS / bash 各自行为不同）→ 可执行扩展名解析（`PATHEXT`）→ PATH → 二进制本身。
   **从最外层往里查**，本轮的答案在第 3 层，而人本能地会去查第 5 层。
2. **用「同一输入、不同消费者」做对照实验**
   同一份环境变量交给 `cmd.exe` 得到正常值、交给 PS 5.1 得到 `.CPL`
   ⇒ 立刻分清是「环境被污染」还是「消费者自身 fallback」。**这是本役破案的关键一步**。
3. **查注册表原始值，别只信进程环境**
   `HKLM\...\Environment\PATHEXT` 正常、`HKCU\...` 该值不存在、而进程里是 `.CPL`
   ⇒ 说明**不是配置问题，是没传下去**。
4. **⚠️ 警惕「绝对路径兜底」的假成功**
   坏 `PATHEXT` 下 `& '.../git.exe' --version` **输出为空且退出码 0** ——
   **静默失败比报错更危险**。判据：`rc == 0` **且** 输出为空 ⇒ 按失败处理，不要当成功。
5. **⭐ 想「升级到新版本解决」时，先做离线便携版对照实测**
   本轮实测：**PS 7.6.6 与 5.1 在 `PATHEXT` 缺失时行为逐字节一致** ⇒ 升级**无改善**。
   做法（**零安装、零改配置**）：下官方便携版 ZIP → 解到**工作区外** → 把同一份环境分别喂给
   新旧两个可执行文件 → **逐项对照**。⚠️ 别用「推断」代替这一步 ——
   我起初写「与 PS 版本无关」其实只是**推论**，补了实测才敢下结论。
   ⇒ **凡「换个版本/换个工具就好了」的结论，都必须有对照实测，不能只有源码阅读。**

**沉淀判据**

> 「某命令不可用」的结论，**必须附「同输入不同消费者」的对照结果**，
> 否则很容易把「某一个 shell 的 fallback 行为」误记成「整体环境缺陷」，
> 进而写进文档、被下游反复引用（这正是本项目「静默过期」家族的形态之一）。