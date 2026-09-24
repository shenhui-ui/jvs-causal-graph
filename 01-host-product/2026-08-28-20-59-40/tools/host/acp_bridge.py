#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""ACP 会话桥 — 用本机 DSH 的 ACP profile 可编程创建 / 列举 / 恢复会话并投递交接材料。

依据:
- 勘察报告 `M1-10-跨会话寻址工具勘察-20260919.md`(§九 端到端实测,十项全绿)
- 上游 `duoduoler-ops/Table-skills` 的 `project-handoff` 机制层不可用(依赖 Codex Hook 事件),
  本桥是**替代实现**:把「新建接续 + 投递交接材料」从手动改为可编程。
- 技能 `jvs-acp-session-bridge`

⚠️ 三条不可省略的事实:
1. **寻址的是 DSH 会话,不是 WorkBuddy 会话**。桥只能让 DSH 侧接续。
2. **不能强制新会话「先只读核验」** —— ACP 无「约定首轮行为」机制,只能靠**开场白软约束**
   (本桥把软约束写进 prompt 模板,但**无法保证对方遵守**)。
3. `session/fork` **实测不可用**(`-32601 Method not found`),故不提供 fork 子命令。

⚠️ 隐私闸门(强制,fail-closed):
投递 = 把材料外发给模型。按 `tools/host/README-privacy.md`,**任何上云 payload 必须先过闸门**。
本桥投递前**自动**调用 `name_scan.py`;命中即拒发;**找不到闸门也拒绝**(不留后门)。

用法:
  python acp_bridge.py probe                                  # 只读:握手 + 打印能力声明
  python acp_bridge.py list [--cwd <dir>] [--limit N]         # 只读:列举会话
  python acp_bridge.py new --prompt-file <f> [--dry-run]      # 新建会话并投递
  python acp_bridge.py resume --session-id <id> --prompt-file <f>
  # 常用开关: --cwd <dir> --timeout <s> --print-reply --json --verbose

返回码: 0=成功; 1=闸门拦截; 2=环境/协议失败; 3=参数错误

本桥**不**推送、**不**安装、**不**改配置、**不**删除任何文件;唯一副作用是 DSH 侧新建会话。
"""

import argparse
import glob
import importlib.util
import io
import json
import os
import subprocess
import sys
import threading
import time

# ---------------------------------------------------------------- 环境自适应
# 不写死版本号:按序号最大者取(与 launch-dsh 技能同一教训 —— 写死路径会随升级失效)
NODE_GLOBS = [
    r"C:\Users\<user>\.workbuddy-ai\binaries\node\versions\*\node.exe",
    r"C:\Program Files\nodejs\node.exe",
]
DSH_GLOBS = [
    r"C:\Users\<user>\.workbuddy-ai\binaries\node\dsh-install-*\node_modules\@deepseek-ai\dsh\lib\bin.js",
]
DSH_HOME_DEFAULT = r"C:\Users\<user>\.dsh"

# ---- PATHEXT 修复（2026-09-19 实测根因，见 README-acp-bridge.md「环境修复」节）
# 现象：ACP 会话内 `git` 全部无法执行（裸名报"无法识别为 cmdlet"，
#       连**绝对路径**调 git.exe 也静默失败、退出码 0）。
# 根因：父进程未设置 PATHEXT 时，**PowerShell 5.1 自身 fallback 到 `.CPL`**
#       （实测：同一环境给 cmd.exe 得到正常长串，给 PS 5.1 得到 `.CPL`）
#       ⇒ PS 不把 `.exe` 当可执行文件。**与 PowerShell 版本无关**。
# 修法：桥在启动 DSH 时显式注入正常 PATHEXT，由 pty 子进程继承。
PATHEXT_DEFAULT = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC"

# name_scan 探测顺序:显式参数 > 环境变量 > 项目内固定路径 > 同目录
NAME_SCAN_GLOBS = [
    r"C:\Users\<user>\Desktop\JVS\01-host-product\2026-08-28-20-59-40\tools\host\name_scan.py",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "name_scan.py"),
]


def child_env():
    """构造启动 DSH 的子进程环境：修 PATHEXT，其余原样继承。

    只在 PATHEXT 缺失或明显异常（不含 .EXE）时覆盖，避免掩盖真实配置。
    """
    env = dict(os.environ)
    cur = env.get("PATHEXT", "")
    if ".EXE" not in cur.upper():
        env["PATHEXT"] = PATHEXT_DEFAULT
    return env


def _pick_latest(patterns):
    """从 glob 模式里取"序号最大"的那个(install-10 > install-9 > install-2)。"""
    cands = []
    for pat in patterns:
        cands.extend(glob.glob(pat))
    if not cands:
        return None

    def sort_key(p):
        # 抽出路径里的数字段(install-4 / 22.22.2-2),按数字序而非字典序
        nums = []
        for part in p.replace("\\", "/").split("/"):
            for tok in part.split("-"):
                if tok.isdigit():
                    nums.append(int(tok))
        return (nums, p)

    return sorted(cands, key=sort_key)[-1]


def find_node():
    n = _pick_latest(NODE_GLOBS)
    if not n:
        raise EnvError("找不到 node.exe(已查 %s)" % NODE_GLOBS)
    return n


def find_dsh():
    d = _pick_latest(DSH_GLOBS)
    if not d:
        raise EnvError("找不到 dsh 入口(已查 %s);用 --dsh 显式指定" % DSH_GLOBS)
    return d


class EnvError(RuntimeError):
    pass


class GateError(RuntimeError):
    pass


# ---------------------------------------------------------------- 隐私闸门
def load_name_scan(explicit=""):
    """加载 name_scan 模块。

    ⚠️ fail-closed 语义:
    - **显式**指定(参数或 JVS_NAME_SCAN)但不存在 ⇒ 直接抛错,**不静默回退**
      （否则「我以为用了自己的表」实际在用默认路径 ⇒ 假安全感）
    - 未显式指定 ⇒ 按默认路径探测;找不到返回 None(调用方拒绝外发)
    """
    for src, val in (("--name-scan", explicit), ("JVS_NAME_SCAN", os.environ.get("JVS_NAME_SCAN", ""))):
        if val:
            if not os.path.isfile(val):
                raise GateError("显式指定的闸门不存在（%s=%s）⇒ 拒绝外发，不回退到默认路径" % (src, val))
            return _load_module(val)
    for c in NAME_SCAN_GLOBS:
        if c and os.path.isfile(c):
            return _load_module(c)
    return None


def _load_module(path):
    spec = importlib.util.spec_from_file_location("name_scan", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._jvs_path = path
    return mod


def gate_check(text, scan_mod, names_json=""):
    """过闸门。返回 (ok, detail)。text 是**将要外发的完整 payload**。"""
    if scan_mod is None:
        raise GateError("隐私闸门 name_scan.py 不可用 ⇒ 拒绝外发(不做「跳过检查」的降级)")
    names = scan_mod.load_names(names_json or "")
    pat = scan_mod.build_pattern(names)
    hits = scan_mod.scan_text(text, pat, names) or []
    if hits:
        return False, hits
    return True, []


# ---------------------------------------------------------------- ACP 客户端
class AcpClient:
    """极简 ACP v1 客户端(JSON-RPC 2.0 over stdin/stdout,换行分帧)。"""

    def __init__(self, node, dsh, home=None, timeout=300):
        self.node = node
        self.dsh = dsh
        self.home = home or DSH_HOME_DEFAULT
        self.timeout = timeout
        self.child = None
        self._rx = ""
        self._pending = {}
        self._lock = threading.Lock()
        self._idc = 0
        self.updates = []          # session/update 通知序列
        self.reply_chunks = []     # agent_message_chunk 文本
        self.stderr_buf = []
        self._alive = True

    # ---- 生命周期
    def start(self):
        self.child = subprocess.Popen(
            [self.node, self.dsh, "--profile", "acp"],
            cwd=self.home,
            env=child_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        return self

    def stop(self):
        self._alive = False
        try:
            if self.child and self.child.stdin:
                self.child.stdin.close()
        except Exception:
            pass
        try:
            if self.child:
                self.child.terminate()
                self.child.wait(timeout=5)
        except Exception:
            try:
                self.child.kill()
            except Exception:
                pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()

    # ---- IO
    def _read_stdout(self):
        try:
            for line in self.child.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                self._dispatch(msg)
        except Exception:
            pass

    def _read_stderr(self):
        try:
            for line in self.child.stderr:
                if line.strip():
                    self.stderr_buf.append(line.rstrip())
        except Exception:
            pass

    def _dispatch(self, msg):
        mid = msg.get("id")
        if mid is not None and mid in self._pending:
            ev, box = self._pending.pop(mid)
            box.append(msg)
            ev.set()
            return
        method = msg.get("method")
        if method == "session/update":
            u = (msg.get("params") or {}).get("update") or msg.get("params") or {}
            kind = u.get("sessionUpdate") or "(未知)"
            self.updates.append(kind)
            if kind == "agent_message_chunk":
                c = u.get("content") or {}
                if c.get("type") == "text" and c.get("text"):
                    self.reply_chunks.append(c["text"])
        elif method:
            self.updates.append("请求:" + method)

    def rpc(self, method, params, timeout=None):
        if not self._alive:
            raise EnvError("连接已关闭")
        with self._lock:
            self._idc += 1
            mid = self._idc
        payload = json.dumps({"jsonrpc": "2.0", "id": mid, "method": method,
                              "params": params}, ensure_ascii=False)
        ev = threading.Event()
        box = []
        self._pending[mid] = (ev, box)
        try:
            self.child.stdin.write(payload + "\n")
            self.child.stdin.flush()
        except Exception as e:
            self._pending.pop(mid, None)
            raise EnvError("写入失败: %s" % e)
        if not ev.wait(timeout or self.timeout):
            self._pending.pop(mid, None)
            raise EnvError("超时(%ss): %s" % (timeout or self.timeout, method))
        return box[0]

    # ---- 便捷
    def initialize(self, name="jvs-acp-bridge"):
        r = self.rpc("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {},
            "clientInfo": {"name": name, "version": "1.0.0"},
        })
        return r

    def reply_text(self):
        return "".join(self.reply_chunks)


# ---------------------------------------------------------------- 开场白模板
# ⚠️ 材料**内联**进 prompt,而不是只给路径。
#    原因:若只给路径,接续会话会自行读文件 ⇒ 内容照样进模型上下文,
#    但这些字节**没经过闸门** ⇒ 闸门形同虚设。内联后「闸门查的 = 实发的」。
# 上游「自动接续」6 步中,第 3 步「接手方先只读核验」在 ACP 下无机制保障 ⇒ 写进软约束。
PROMPT_TEMPLATE = """你是一个项目的**接续会话**。请先只读核验,再等我指令 —— 这是硬要求。

## 第一步(必须):只读核验,不要修改任何文件
读完下方材料后,**用不超过 15 行**回报:
① 你读到了什么(每份材料一句) ② 你认为当前阶段最重要的一件事 ③ 你发现的任何矛盾或缺口。

## 边界(不得越过)
- 不得 `git push`、不得初始化仓库、不得安装依赖、不得删除文件、不得改配置
- 收尾改动必须提交(本环境会回滚未提交改动)
{note}
{extra}
读完后**停下等我指令**,不要自行开工。

============================================================
# 交接材料
============================================================
{materials}
"""


def read_material(path, max_bytes):
    """读材料并内联。返回 (显示名, 内容, 是否截断)。"""
    if not os.path.isfile(path):
        raise EnvError("材料不存在: %s" % path)
    with io.open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    raw = text.encode("utf-8")
    truncated = False
    if max_bytes and len(raw) > max_bytes:
        text = raw[:max_bytes].decode("utf-8", errors="ignore")
        truncated = True
    return path, text, truncated


def build_prompt(material_paths, note="", extra="", max_bytes=200000):
    blocks = []
    metas = []
    for p in material_paths:
        name, text, trunc = read_material(p, max_bytes)
        blocks.append("----- 材料: %s%s -----\n%s"
                      % (name, "（已截断）" if trunc else "", text.rstrip()))
        metas.append({"path": name, "bytes": len(text.encode("utf-8")), "truncated": trunc})
    mats = "\n\n".join(blocks) if blocks else "(未指定材料)"
    prompt = PROMPT_TEMPLATE.format(
        materials=mats,
        note=("\n" + note.strip() + "\n") if note.strip() else "",
        extra=(extra.strip() + "\n") if extra.strip() else "",
    )
    return prompt, metas


# ---------------------------------------------------------------- 子命令
def _boot(args):
    node = args.node or find_node()
    dsh = args.dsh or find_dsh()
    return AcpClient(node, dsh, home=args.home, timeout=args.timeout)


def _emit(obj, as_json, text_fn=None):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    elif text_fn:
        print(text_fn(obj))


def cmd_probe(args):
    """只读:握手 + 能力声明。**不创建会话、不调模型。**"""
    with _boot(args) as c:
        r = c.initialize("jvs-acp-bridge-probe")
        if r.get("error"):
            print("[FAIL] initialize: %s" % json.dumps(r["error"], ensure_ascii=False))
            return 2
        res = r.get("result") or {}
        caps = res.get("agentCapabilities") or {}
        sess = (caps.get("sessionCapabilities") or res.get("sessionCapabilities") or {})
        out = {
            "ok": True,
            "protocolVersion": res.get("protocolVersion"),
            "agentInfo": res.get("agentInfo"),
            "authMethods": res.get("authMethods"),
            "sessionCapabilities": sess,
            "forkSupported": "fork" in sess,
            "modelCalled": False,
            "pathextInjected": os.environ.get("PATHEXT", ""),
            "pathextForChild": child_env().get("PATHEXT", ""),
        }
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print("协议版本   : %s" % out["protocolVersion"])
            print("agent      : %s" % json.dumps(out["agentInfo"], ensure_ascii=False))
            print("认证方式   : %s%s" % (out["authMethods"],
                                        "（免凭证）" if not out["authMethods"] else ""))
            print("会话能力   : %s" % sorted(sess.keys()))
            print("fork 支持  : %s" % ("是" if out["forkSupported"] else "**否**（实测 -32601）"))
            print("调用模型   : 否（纯只读）")
            cur = os.environ.get("PATHEXT", "")
            ok_env = ".EXE" in cur.upper()
            print("本进程 PATHEXT : %s%s" % (cur or "(未设置)",
                                            "" if ok_env else "  ← ⚠️ 异常，子会话 git 会失效"))
            print("子进程 PATHEXT : %s" % child_env().get("PATHEXT", ""))
        return 0


def cmd_list(args):
    """只读:列举会话。不传 cwd = 全量。"""
    with _boot(args) as c:
        c.initialize("jvs-acp-bridge-list")
        params = {}
        if args.cwd:
            params["cwd"] = args.cwd
        r = c.rpc("session/list", params)
        if r.get("error"):
            print("[FAIL] session/list: %s" % json.dumps(r["error"], ensure_ascii=False))
            return 2
        sessions = ((r.get("result") or {}).get("sessions")) or []
        total = len(sessions)
        if args.limit:
            sessions = sessions[: args.limit]
        if args.json:
            print(json.dumps({"ok": True, "total": total, "sessions": sessions},
                             ensure_ascii=False, indent=2))
        else:
            print("会话总数: %d%s" % (total, "（已截断显示前 %d）" % args.limit if args.limit else ""))
            for s in sessions:
                print("  %s  | %s" % (s.get("sessionId"), s.get("cwd")))
            print("\n注: session/list 不是会话全集(磁盘数 > 列表数;精确口径未定,见勘察报告 §九)")
        return 0


def _deliver(args, session_id=None):
    """公共投递逻辑:读材料(内联) → 拼开场白 → 过闸门 → 创建或恢复 → prompt。

    ⭐ 材料**全文内联**进 prompt,闸门查的就是**实发字节**。
    """
    args.cwd = args.cwd or os.getcwd()

    # 1) 组装:--material 列材料,--prompt-file 作为「交接区记录」附注(可选)
    paths = list(args.material or [])
    if args.prompt_file:
        if not os.path.isfile(args.prompt_file):
            print("[FAIL] 附注文件不存在: %s" % args.prompt_file, file=sys.stderr)
            return 3
        paths.append(args.prompt_file)
    if not paths:
        print("[FAIL] 至少需要一个 --material（或 --prompt-file）", file=sys.stderr)
        return 3

    try:
        prompt, metas = build_prompt(paths, extra=args.extra or "",
                                     max_bytes=args.max_bytes)
    except EnvError as e:
        print("[FAIL] %s" % e, file=sys.stderr)
        return 3
    missing = [m["path"] for m in metas if not os.path.isfile(m["path"])]

    # 2) 隐私闸门(强制,fail-closed)—— 投递=外发
    try:
        scan = load_name_scan(args.name_scan or "")
        ok, hits = gate_check(prompt, scan, args.names or "")
    except GateError as e:
        print("[FAIL] %s" % e, file=sys.stderr)
        return 1
    if not ok:
        print("[FAIL] 隐私闸门拦截 —— 命中 %d 处，拒绝外发：" % len(hits), file=sys.stderr)
        for h in hits[:20]:
            if isinstance(h, (tuple, list)) and len(h) == 2:
                print("   第 %s 行: %s" % (h[0], h[1]), file=sys.stderr)
            else:
                print("   %s" % h, file=sys.stderr)
        print("⇒ 请先脱敏（tools/causal/desensitize_feishu.py），勿跳过闸门。", file=sys.stderr)
        return 1
    if args.verbose:
        print("[闸门] 放行：%d B 材料无命中（%s）"
              % (len(prompt.encode("utf-8")), getattr(scan, "_jvs_path", "?")), file=sys.stderr)

    # 3) 干跑
    if args.dry_run:
        print("=== DRY RUN（未创建会话、未调模型）===")
        print("闸门      : 放行（查的就是下述 %d B 全文）" % len(prompt.encode("utf-8")))
        print("目标      : %s" % ("恢复 " + session_id if session_id else "新建会话"))
        print("cwd       : %s" % args.cwd)
        for m in metas:
            print("材料      : %s  %d B%s" % (m["path"], m["bytes"],
                                             "  ⚠️已截断" if m["truncated"] else ""))
        print("--- prompt 全文 ---")
        print(prompt)
        return 0

    # 4) 真投递
    with _boot(args) as c:
        c.initialize("jvs-acp-bridge")
        t0 = time.time()
        if session_id:
            r = c.rpc("session/resume", {"sessionId": session_id, "cwd": args.cwd})
            if r.get("error"):
                print("[FAIL] session/resume: %s" % json.dumps(r["error"], ensure_ascii=False))
                print("提示: 会话可能仍在活跃;session/resume 不能对活跃会话调用。", file=sys.stderr)
                return 2
            sid = session_id
        else:
            r = c.rpc("session/new", {"cwd": args.cwd, "mcpServers": []})
            if r.get("error"):
                print("[FAIL] session/new: %s" % json.dumps(r["error"], ensure_ascii=False))
                return 2
            sid = (r.get("result") or {}).get("sessionId")
        # prompt
        pr = c.rpc("session/prompt", {
            "sessionId": sid,
            "prompt": [{"type": "text", "text": prompt}],
        }, timeout=args.prompt_timeout)
        elapsed = time.time() - t0
        reply = c.reply_text()

        if pr.get("error"):
            print("[FAIL] session/prompt: %s" % json.dumps(pr["error"], ensure_ascii=False))
            print("会话已创建/恢复，sessionId = %s（未关闭，可手动续投）" % sid)
            return 2
        stop = (pr.get("result") or {}).get("stopReason")

        if args.close:
            c.rpc("session/close", {"sessionId": sid}).get("result")

        out = {
            "ok": True,
            "sessionId": sid,
            "stopReason": stop,
            "elapsedSec": round(elapsed, 1),
            "replyBytes": len(reply.encode("utf-8")),
            "promptBytes": len(prompt.encode("utf-8")),
            "cwd": args.cwd,
            "closed": bool(args.close),
            "updateKinds": sorted(set(c.updates)),
        }
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print("✅ 投递成功")
            print("sessionId  : %s" % sid)
            print("stopReason : %s" % stop)
            print("耗时       : %.1fs" % elapsed)
            print("prompt     : %d B ｜ 回复 %d B" % (out["promptBytes"], out["replyBytes"]))
            print("事件序列   : %s" % " | ".join(out["updateKinds"]))
            print("会话状态   : %s" % ("已关闭" if args.close else "保持活跃"))
            if args.print_reply:
                print("\n--- 接续会话的回复 ---")
                print(reply or "(无文本)")
            print("\n续投命令   : python acp_bridge.py resume --session-id %s --material <f>" % sid)
        return 0


def cmd_new(args):
    return _deliver(args, session_id=None)


def cmd_resume(args):
    if not args.session_id:
        print("[FAIL] 需要 --session-id", file=sys.stderr)
        return 3
    return _deliver(args, session_id=args.session_id)


# ---------------------------------------------------------------- CLI
def build_parser():
    ap = argparse.ArgumentParser(
        description="ACP 会话桥（用本机 DSH 的 ACP profile 创建/列举/恢复会话）")
    # 全局参数：既可前置也可后置。
    # ⚠️ default 必须用 SUPPRESS —— 否则子解析器的默认值会把主解析器已设的值覆盖掉
    #    （实测：`--json list` 前置时不生效）。
    SUP = argparse.SUPPRESS
    glob = argparse.ArgumentParser(add_help=False)
    glob.add_argument("--node", default=SUP, help="node.exe 路径（默认自动探测）")
    glob.add_argument("--dsh", default=SUP, help="dsh 入口 lib/bin.js（默认自动探测）")
    glob.add_argument("--home", default=SUP, help="DSH_HOME（默认 ~/.dsh）")
    glob.add_argument("--name-scan", default=SUP, help="name_scan.py 路径（默认自动探测）")
    glob.add_argument("--names", default=SUP, help="闸门用的身份表 json")
    glob.add_argument("--json", action="store_true", default=SUP, help="机读输出")
    glob.add_argument("--verbose", action="store_true", default=SUP)
    ap.add_argument("--node", default=SUP, help=SUP)
    ap.add_argument("--dsh", default=SUP, help=SUP)
    ap.add_argument("--home", default=SUP, help=SUP)
    ap.add_argument("--name-scan", default=SUP, help=SUP)
    ap.add_argument("--names", default=SUP, help=SUP)
    ap.add_argument("--json", action="store_true", default=SUP, help="机读输出")
    ap.add_argument("--verbose", action="store_true", default=SUP)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", parents=[glob],
                       help="只读:握手 + 能力声明（不创建会话、不调模型）")
    p.add_argument("--timeout", type=float, default=60)
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("list", parents=[glob], help="只读:列举会话")
    p.add_argument("--cwd", default="", help="按工作目录过滤（不传=全量）")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--timeout", type=float, default=60)
    p.set_defaults(fn=cmd_list)

    for name, fn, helptext in (("new", cmd_new, "新建会话并投递交接材料"),
                               ("resume", cmd_resume, "恢复到已有会话并投递")):
        p = sub.add_parser(name, parents=[glob], help=helptext)
        p.add_argument("--material", action="append", default=[],
                       help="交接材料（可多次；**全文内联进 prompt 并过闸门**）")
        p.add_argument("--prompt-file", default="",
                       help="附加说明文件（可选；同样内联并过闸门）")
        p.add_argument("--session-id", default="", help="resume 时必填")
        p.add_argument("--cwd", default="", help="会话工作目录（默认当前目录）")
        p.add_argument("--extra", default="", help="追加到开场白末尾的指令")
        p.add_argument("--max-bytes", type=int, default=200000,
                       help="单份材料内联上限（默认 200,000 B，超出截断）")
        p.add_argument("--dry-run", action="store_true", help="只打印，不创建、不调模型")
        p.add_argument("--print-reply", action="store_true", help="打印接续会话的回复")
        p.add_argument("--close", action="store_true", help="投递后关闭会话")
        p.add_argument("--timeout", type=float, default=120, help="握手/管理调用超时")
        p.add_argument("--prompt-timeout", type=float, default=600, help="prompt 超时（模型推理）")
        p.set_defaults(fn=fn)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    # SUPPRESS 语义：未指定的参数不会出现在 args 上 ⇒ 统一兜底默认值
    for k, v in (("node", ""), ("dsh", ""), ("home", DSH_HOME_DEFAULT),
                 ("name_scan", ""), ("names", ""), ("json", False), ("verbose", False),
                 ("cwd", ""), ("timeout", 60)):
        if not hasattr(args, k):
            setattr(args, k, v)
    args.home = args.home or DSH_HOME_DEFAULT
    try:
        return args.fn(args)
    except GateError as e:
        print("[FAIL] %s" % e, file=sys.stderr)
        return 1
    except EnvError as e:
        print("[FAIL] 环境错误: %s" % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n[中断]", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())