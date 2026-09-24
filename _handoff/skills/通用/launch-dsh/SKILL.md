---
name: launch-dsh
description: 唤起本机 DSH（DeepSeek Harness）——启动 Web 界面、跑一次性 headless 任务或进入 TUI，并排查启动失败。当用户提到"唤起/启动 DSH""dsh web""DeepSeek Harness""dsh headless""dsh 打不开"时使用。
agent_created: true
---

# 唤起 DSH（DeepSeek Harness）

## 环境事实（本机）

| 项 | 值 |
|----|----|
| 包名 | `@deepseek-ai/dsh` |
| 安装位置 | `C:\Users\<user>\.workbuddy-ai\binaries\node\dsh-install-2`（0.1.1-rc.2）、`dsh-install-3`（0.1.2-rc.1）、**`dsh-install-4`（0.1.5-rc.1，当前默认）** |
| 版本核对 | 2026-09-10 核对：npm `latest = 0.1.5-rc.1`，与 dsh-install-4 一致。DSH 迭代很快，**版本随时会变，用前先查 `npm view @deepseek-ai/dsh dist-tags` 和本机 `dsh-install-*`** |
| DSH_HOME | `C:\Users\<user>\.dsh`（settings / profiles / sessions） |
| 入口 | `<dsh-install-N>\node_modules\@deepseek-ai\dsh\lib\bin.js` |
| 可用 profiles | `web`、`headless` |
| 默认端口 | 3080 |

## 唤起方式

### 1. Web 界面（最常用）

用户自己双击即可：`C:\Users\<user>\.dsh\dsh-web.cmd`。
该启动器会自动探测 node 版本和最新的 `dsh-install-*`，node 升级后不会失效。

支持两个可选参数（默认工作区已实测可用，跨盘符 D:\ 无问题）：

```bat
dsh-web.cmd                          :: port 3080, workspace D:\<user>\Documents
dsh-web.cmd 3080                     :: 指定端口
dsh-web.cmd 3080 D:\some\project     :: 指定端口 + 工作区（推荐：一次设对，省得在 UI 里选）
```

**务必设对工作区**：DSH 把启动目录当作 Web UI 的 workspace root。若启动目录是 `C:\Users\<user>\.dsh`，默认工作区就变成 DSH 自己的配置目录（sessions/settings 全在里面），用起来很别扭。

配套文件 `%DSH_HOME%\dsh-pick-install.js`：按**真实版本号**挑最新的 `dsh-install-*`，避免 `for /d` 字母序在 `dsh-install-10` 时选错。`.cmd` 优先调它，缺失才回退 `for /d`。

单独验证这个选择逻辑（无需 cmd，可直接在 bash 里跑）：

```bash
node "C:/Users/<user>/.dsh/dsh-pick-install.js"   # 应输出 dsh-install-4
```

需要在会话里代启动时，用 Bash 直接跑 node（**这是本环境唯一可靠的方式**）：

```bash
cd "C:/Users/<user>/.dsh" \
  && export DSH_HOME="C:/Users/<user>/.dsh" \
  && "C:/Users/<user>/.workbuddy-ai/binaries/node/versions/22.22.2-2/node.exe" \
     "C:/Users/<user>/.workbuddy-ai/binaries/node/dsh-install-4/node_modules/@deepseek-ai/dsh/lib/bin.js" \
     web --no-open --host 127.0.0.1 --port 3080
```

- 必须 `run_in_background=true`——服务常驻不返回。
- **持久性（重要）**：这样启动的实例是 bash 后台任务的子进程，**后台任务一结束就会被回收**，DSH 服务随之停止（已实测：两个实例都因此终止）。会话内启动只适合临时用；**要长期挂着，必须双击 `dsh-web.cmd`** 用独立控制台窗口启动，它不依赖 WorkBuddy 会话。
- 控制台输出形如 `dsh web: http://127.0.0.1:3080/?token=xxx`，**必须带 token 访问**，直接访问 `/` 返回 401；token 每次启动随机变化。
- 拿到 URL 后用 `present_files` 打开，用户可直接在预览面板使用。

### 2. 一次性任务（跑完即退）

```bash
dsh --profile headless "任务描述"
```

### 3. 其他 profile

官方入口模式（`README.zh.md`）只有这些：`--profile <name>`、`acp`、`headless "job"`、`sdk`、`sdk-minimal`，以及 `web`（`--profile web` 的别名）。

```bash
dsh --profile headless "任务描述"   # 跑完打印最终答案并退出
dsh --dump-config                   # 只打印组合后的配置树，不启动
dsh plugin --profile <name> <pnpm args>   # 管理某 profile 的插件
```

**注意：`tui` profile 并不存在。** `dsh --help` 的示例里出现 `--profile tui` 只是假设性示例（原文写着 "assuming the tui profile is installed"），本机 `profiles/` 下只有 `web` 和 `headless`。不要照抄示例去启动 tui。

## 官方信息（已核实，2026-09-07）

- 仓库：<https://github.com/deepseek-ai/deepseek-harness>，MIT，基于 Cordis，标语 "Everything is a Plugin"
- 文档站：<https://deepseek-harness.github.io/deepseek-harness/>
- 官方推荐唤起方式：`npx @deepseek-ai/dsh web`（默认 127.0.0.1:3080，本机会自动开浏览器；`--no-open` 只起服务）
- **版本（2026-09-10 核对）**：npm `latest = 0.1.5-rc.1`，本机 `dsh-install-4` 同版本 → 已是最新。
  注：2026-09-07 时 latest 还是 0.1.2-rc.1，**三天后就跳到 0.1.5-rc.1**，DSH 迭代极快。
  **不要相信文档里写死的版本号**，动手前先跑：
  ```bash
  npm view @deepseek-ai/dsh dist-tags          # 官方最新
  ls -d .../binaries/node/dsh-install-*        # 本机装了哪些
  ```
  `dsh-web.cmd` 的自适应逻辑会自动挑序号最大的 `dsh-install-*`，所以升级后无需改脚本——这正是当初不写死路径的原因。
- **workspace 根目录 = 启动 dsh 时所在的工作目录**（官方指南原文）。想让 DSH 操作哪个项目，就在哪个目录下启动。
- Web UI 首次使用：设置 → 模型 填 API Key（不需要重启），然后**必须先"选择工作区"**，未选中工作区时会话输入框不可用。
- 一次性 token 鉴权是 0.1.2-rc.1 的破坏性变更（"网络访问 Web 界面时启用链接中的一次性 token 认证鉴权"）。

## 排障

**启动后日志只有起始行、没有 URL** → 脚本里的 node 路径已失效。确认实际版本：

```bash
ls "C:/Users/<user>/.workbuddy-ai/binaries/node/versions/"
cat "C:/Users/<user>/.workbuddy-ai/binaries/node/versions/current"   # 9 字节版本号，无换行
```

官方生成的启动脚本会把 node 路径写死成 `versions\22.22.2-1` 之类，版本被清理后即失败。修完路径后建议保留自适应逻辑（读 `versions\current`）。

**访问返回 401** → 少了 token，用启动输出里的完整 URL。

**端口被占用** → 换端口，或先停旧进程：

```bash
netstat -ano | grep ":3080"     # 找 PID
taskkill //PID <pid> //F
```

**日志**：`C:\Users\<user>\.dsh\web-task.log`（UTF-16，旧条目在命令行下查看可能显示为带空格的字符，属正常）。

## 本环境的坑（重要）

- **不要用 PowerShell 工具启动**：该宿主会吞掉原生进程 stdout，连 `node -v` 都没回显，服务看似秒退。
- **PowerShell 5.1 写脚本时注意**：
  - `.ps1` 含中文必须存为 **UTF-8 with BOM**，否则被按 GBK 解析而报语法错。
  - `$args`、`$Host` 是自动变量，不能当普通变量名用。
  - 原生命令不要放在管道中间（`& node ... 2>&1 | ForEach-Object`）→ `CantActivateDocumentInPipeline`。
  - `Start-Process` 在环境里存在大小写不同的同名变量（如 `http_proxy` / `HTTP_PROXY`）时会抛 `ArgumentException`。
- **不能从 Bash 或 PowerShell 工具调用 `cmd.exe`**（安全策略拦截），所以 `.cmd` 无法在本环境端到端测试，只能静态检查 + 逐项验证其解析出的路径。
