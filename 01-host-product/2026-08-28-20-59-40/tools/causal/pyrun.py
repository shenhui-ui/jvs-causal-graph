#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""pyrun.py — pwsh 环境下运行 python 脚本的输出捕获包装器。

背景(2026-09-02)：本机 pwsh 调用原生 exe 时 stdout/stderr/退出码不会返回给 pwsh
（进程正常执行、副作用正常，仅输出管道丢失）。bash → exe 正常。

用法（在 pwsh 中）:
  & $py "C:\...\tools\causal\pyrun.py" "目标脚本.py" [目标脚本的参数...]

输出: 目标脚本同目录生成 pyrun-result.log，内容为:
  === EXIT CODE ===
  <退出码>
  === STDOUT ===
  <标准输出>
  === STDERR ===
  <标准错误>
之后用 read 工具读取该 log 即可。
"""
import io
import os
import subprocess
import sys


def main():
    if len(sys.argv) < 2:
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pyrun-result.log")
        _write(log_path, "=== EXIT CODE ===\n2\n=== STDOUT ===\n\n=== STDERR ===\nusage: pyrun.py <script> [args...]\n")
        return 2
    script = sys.argv[1]
    args = [sys.executable, "-X", "utf8", script] + sys.argv[2:]
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=os.path.dirname(os.path.abspath(script)) or None)
    log_path = os.path.join(os.path.dirname(os.path.abspath(script)), "pyrun-result.log")
    _write(log_path,
           f"=== EXIT CODE ===\n{r.returncode}\n"
           f"=== STDOUT ===\n{r.stdout}\n"
           f"=== STDERR ===\n{r.stderr}\n")
    return 0


def _write(path, content):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(content)


if __name__ == "__main__":
    raise SystemExit(main())
