#!/usr/bin/env bash
# 安装 openclaw gateway 常驻服务（Windows 上 = 计划任务）
#
# 前置条件：已在 WorkBuddy「安全中心 → 命令安全 → 程序黑名单」中移除 schtasks.exe
#          （未移除时本脚本第 2 步会被沙箱拦下，且不可绕过）
# 用法：bash install-openclaw-gateway.sh
set -u

# openclaw 要求 node >=22.22.3 <23 / >=24.15.0 <25 / >=25.9.0；
# 托管 node 为 v22.22.2（不满足），故把 PATH 指向系统 node v24。
export PATH="/c/Program Files/nodejs:$PATH"

echo "=== 1) 前置检查 ==="
printf '  node     : '; node --version 2>&1
printf '  openclaw : '; timeout -k 15 60 openclaw --version 2>&1 | head -1
printf '  gateway  : '; timeout -k 15 60 openclaw gateway status 2>&1 | grep -i "^Service:" | head -1

echo
echo "=== 2) 安装 gateway 服务 ==="
timeout -k 15 300 openclaw gateway install
rc=$?
echo "  install rc=$rc"

echo
echo "=== 3) 验证 ==="
timeout -k 15 120 openclaw gateway status 2>&1 | head -25

echo
echo "=== 4) 判据（不要只看命令是否报错）==="
echo "  · Service 行应为 Scheduled Task (installed) 或等价；"
echo "  · Runtime 应为 node（不是 unknown / spawn EPERM）；"
echo "  · Connectivity probe 应能连上 ws://127.0.0.1:18789（不再是 ECONNREFUSED）。"
