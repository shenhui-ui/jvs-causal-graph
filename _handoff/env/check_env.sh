#!/usr/bin/env bash
# JVS 接手环境自检 —— 复制到新会话后先跑这个
# 用法: bash _handoff/env/check_env.sh
set -u

echo "=============================================="
echo " JVS 环境自检  ($(date '+%Y-%m-%d %H:%M'))"
echo "=============================================="

PASS=0; FAIL=0; WARN=0
ok(){   echo "  [OK]   $1"; PASS=$((PASS+1)); }
bad(){  echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
warn(){ echo "  [WARN] $1"; WARN=$((WARN+1)); }

# ---------- 1. 解释器 ----------
echo
echo "[1] Python 解释器"
# 解释器路径可覆盖：设 JVS_PY 即换机可用；不设则与既有硬编码值逐字节相同。
PY="${JVS_PY:-C:/Users/<user>/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe}"
if [ -x "$PY" ]; then
  ok "托管解释器 3.13.12 存在"
  echo "         $("$PY" --version 2>&1)"
else
  warn "托管解释器不在预期路径；尝试系统 python"
  if command -v python >/dev/null 2>&1; then
    PY="$(command -v python)"; ok "回退到 python: $(python --version 2>&1)"
  else
    bad "未找到可用 python"
  fi
fi

# ---------- 2. 关键脚本可编译 ----------
echo
echo "[2] 关键脚本语法可编译"
TOOLS="01-host-product/2026-08-28-20-59-40/tools"
for f in "$TOOLS/causal/s4_pipeline.py" "$TOOLS/causal/llm_judge.py" \
         "$TOOLS/causal/relay_call.py" "$TOOLS/causal/targeted_round.py" \
         "$TOOLS/eval/eval_m1.py" "$TOOLS/host/name_scan.py"; do
  if [ -f "$f" ]; then
    if "$PY" -m py_compile "$f" >/dev/null 2>&1; then ok "$(basename "$f")"; else bad "$(basename "$f") 编译失败"; fi
  else
    warn "缺失 $f"
  fi
done

# ---------- 3. 离线回归 ----------
echo
echo "[3] 离线回归（不调模型）"
if [ -f "$TOOLS/causal/test_llm_judge.py" ]; then
  out=$(cd "$TOOLS/causal" && "$PY" -B test_llm_judge.py 2>&1 | tail -3)
  if echo "$out" | grep -qiE "^(OK|Ran [0-9]+ test)"; then
    ok "test_llm_judge 通过"
  else
    warn "test_llm_judge 非通过态: $(echo "$out" | head -1)"
  fi
else
  warn "未找到 test_llm_judge.py"
fi

# ---------- 4. 数据资产就位 ----------
echo
echo "[4] 数据资产就位"
HMD="01-host-product/2026-08-28-20-59-40/docs/host_memory_dump"
for f in "$HMD/all-real-events.jsonl" "$HMD/all-real-edges-final.jsonl" \
         "$HMD/causal_graph_final_v3.db" "$HMD/query-set-real-v1.jsonl" \
         "$HMD/m1_10_baseline_final/gold.jsonl"; do
  if [ -f "$f" ]; then ok "$(basename "$f")"; else warn "缺失 $(basename "$f")"; fi
done

# ---------- 5. 凭证闸门 ----------
echo
echo "[5] 模型凭证（不落盘，走环境变量）"
if [ -n "${RELAY_KEY:-}" ]; then ok "RELAY_KEY 已设置（长度 ${#RELAY_KEY}）"
elif [ -n "${PROBE_KEY:-}" ]; then ok "PROBE_KEY 已设置（长度 ${#PROBE_KEY}）"
else warn "RELAY_KEY / PROBE_KEY 均未设置 —— 凡会调模型的脚本将中止（离线脚本不受影响）"; fi

# ---------- 6. 工具链外部依赖 ----------
echo
echo "[6] 外部工具依赖"
if command -v node >/dev/null 2>&1; then ok "node $(node --version 2>&1)"; else warn "未找到 node"; fi
if command -v openclaw >/dev/null 2>&1; then
  ocv="$(openclaw --version 2>&1 | head -1)"
  # ⚠️ 只检查「命令存在」会把「版本不满足」误判为 OK —— openclaw 报版本错时 rc 仍为 0。
  if echo "$ocv" | grep -q "is required"; then
    warn "openclaw 版本不满足：$ocv（需系统 node v24；裸跑 embedded fallback 不受影响）"
  else
    ok "openclaw $ocv"
  fi
else warn "未找到 openclaw（W3 已实测可裸跑 embedded fallback）"; fi
if command -v git >/dev/null 2>&1; then ok "git $(git --version 2>&1 | awk '{print $3}')"; else warn "未找到 git"; fi

# ---------- 7. 迁移完整性 ----------
echo
echo "[7] 迁移完整性（原路径 Junction 是否有效）"
for p in "C:/Users/<user>/host-workspace/2026-08-28-20-59-40" \
         "C:/Users/<user>/host-workspace/2026-09-08-20-30-19" \
         "C:/Users/<user>/host-workspace/restricted-review" \
         "C:/Users/<user>/host-workspace/outputs"; do
  if [ -d "$p" ]; then ok "可达 $(basename "$p")"; else bad "不可达 $p（需重建 Junction，见 MIGRATION-RECORD.md）"; fi
done

echo
echo "=============================================="
echo " 结果: OK=$PASS  WARN=$WARN  FAIL=$FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo " ⚠ 存在失败项，先修复再开工"
else
  echo " ✅ 环境就绪，可开工"
fi
echo "=============================================="