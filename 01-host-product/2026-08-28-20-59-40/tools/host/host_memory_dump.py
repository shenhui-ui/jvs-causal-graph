#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""宿主记忆抓取器(S2 用) — 把 OpenClaw 记忆文件导出为 JSONL,供事件抽取(S3)使用。

依据:
- 《B01-链A-PoC任务书》S2:遍历 memory/ 4 类文件 + 会话目录,输出 host_memory_dump/*.jsonl
- 本机安装记录(2026-08-29):~\.openclaw\workspace(记忆)、~\.openclaw\agents\main\sessions(会话)

用法(Windows PowerShell,用真实 Python,本机 python 是 WindowsApps 桩):
  & "C:\Users\<user>\.venv-html-to-docx\Scripts\python.exe" host_memory_dump.py --out "C:\Users\<user>\host-workspace\2026-08-28-20-59-40\docs\host_memory_dump"

输出:每个文件一条 JSONL 记录:
  {"source": "<相对路径>", "kind": "daily|long_term|user_profile|dreams|dreams_state|session|other",
   "ts": "YYYY-MM-DD" 或 null, "source_hash": "sha256", "content": "<全文>"}
另输出 summary.txt(统计)与 memory_search_probe.md(S2 通路笔记模板)。

注意:
1. 只读,不改任何文件;不读配置文件/密钥。
2. hash 为原文 sha256,供证据绑定;脱敏由调用方在导出前处理(B05 规则)。
3. memory_search corpus=all 的通路验证需要 API key,见 README 说明,本脚本不调用。
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime

KINDS = {
    "USER.md": "user_profile",
    "MEMORY.md": "long_term",
    "DREAMS.md": "dreams",
}

def kind_of(rel_path: str) -> str:
    base = os.path.basename(rel_path)
    if base in KINDS:
        return KINDS[base]
    if re.match(r"\d{4}-\d{2}-\d{2}", base):
        return "daily"
    if ".dreams" in rel_path:
        return "dreams_state"
    if os.sep + "sessions" + os.sep in rel_path or "sessions" in rel_path.split(os.sep):
        return "session"
    return "other"

def ts_of(rel_path: str, mtime: float) -> str | None:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", os.path.basename(rel_path))
    if m:
        return m.group(1)
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")

def sha256_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()

def walk_md(root: str):
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower().endswith((".md", ".txt")):
                yield os.path.join(dirpath, fn)

def main() -> int:
    home = os.path.expanduser("~")
    ap = argparse.ArgumentParser(description="OpenClaw 记忆文件只读导出(S2)")
    ap.add_argument("--workspace", default=os.path.join(home, ".openclaw", "workspace"),
                    help="OpenClaw workspace 目录(含 memory/)")
    ap.add_argument("--sessions", default=os.path.join(home, ".openclaw", "agents", "main", "sessions"),
                    help="会话目录(可选)")
    ap.add_argument("--out", default="host_memory_dump", help="输出目录")
    ap.add_argument("--max-file-kb", type=int, default=256,
                    help="超限文件跳过并记录(默认 256KB,防会话转录过大)")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    jsonl_path = os.path.join(args.out, "memory_dump.jsonl")
    summary = {"files": 0, "skipped": 0, "kinds": {}, "bytes": 0, "dirs": []}

    with open(jsonl_path, "w", encoding="utf-8") as f:
        roots = [("workspace", args.workspace), ("sessions", args.sessions)]
        for label, root in roots:
            if not os.path.isdir(root):
                print(f"[warn] 目录不存在,跳过: {root}", file=sys.stderr)
                continue
            for path in sorted(walk_md(root)):
                try:
                    size = os.path.getsize(path)
                    if size > args.max_file_kb * 1024:
                        summary["skipped"] += 1
                        print(f"[warn] 超限跳过({args.max_file_kb}KB): {path}", file=sys.stderr)
                        continue
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read()
                    rel = os.path.relpath(path, root)
                    rec = {
                        "source": f"{label}/{rel}",
                        "kind": kind_of(rel),
                        "ts": ts_of(rel, os.path.getmtime(path)),
                        "source_hash": sha256_text(content),
                        "content": content,
                    }
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    summary["files"] += 1
                    summary["bytes"] += size
                    summary["kinds"][rec["kind"]] = summary["kinds"].get(rec["kind"], 0) + 1
                except Exception as e:  # noqa: BLE001
                    summary["skipped"] += 1
                    print(f"[warn] 读取失败: {path} ({e})", file=sys.stderr)

    with open(os.path.join(args.out, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(f"生成时间: {datetime.now().isoformat(timespec='seconds')}\n")
        f.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    # S2 通路笔记模板(memory_search 需 API key 后人工补录)
    probe_path = os.path.join(args.out, "memory_search_probe.md")
    if not os.path.exists(probe_path):
        with open(probe_path, "w", encoding="utf-8") as f:
            f.write("# S2 通路笔记(memory_search / wiki)\n\n"
                    "> 由 host_memory_dump.py 生成。以下三项请在配置好模型 API key 后手工执行并贴回结果:\n\n"
                    "1. 重建索引:`openclaw memory status --index --agent main`(观察向量索引是否就绪)\n"
                    "2. 检索验证:`openclaw memory search \"支付超时\"`(如有此子命令;无则用 `openclaw --help` 找检索入口,记下实际命令)\n"
                    "3. wiki 验证:`openclaw wiki status` / `openclaw wiki get <page>`(实际子命令以 `--help` 为准;记录 claims 结构与 vault 路径)\n\n"
                    "记录要点:每入口的「怎么进、能拿什么」+ 3 个脏数据样例(重复/截断/含 prompt 残留)。\n")

    print(f"[ok] 导出 {summary['files']} 条(跳过 {summary['skipped']}): {jsonl_path}")
    print(f"[ok] 分类统计: {json.dumps(summary['kinds'], ensure_ascii=False)}")
    print("[info] 下一步:打开 memory_search_probe.md,按清单验证检索/wiki 通路(需 API key)。")
    return 0

if __name__ == "__main__":
    sys.exit(main())
