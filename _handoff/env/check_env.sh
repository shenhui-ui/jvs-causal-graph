#!/usr/bin/env bash
# JVS 接手环境自检 —— 复制到新会话后先跑这个
# 用法: bash _handoff/env/check_env.sh
set -u

echo "=============================================="
echo " JVS 环境自检  ($(date '+%Y-%m-%d %H:%M'))"
echo "=============================================="

PASS=0; FAIL=0; WARN=0; SKIP=0
ok(){   echo "  [OK]   $1"; PASS=$((PASS+1)); }
bad(){  echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
warn(){ echo "  [WARN] $1"; WARN=$((WARN+1)); }
skip(){ echo "  [SKIP] $1"; SKIP=$((SKIP+1)); }

# ============================================================
# ⭐ 公开层 / 私有层自动判定（2026-09-24 新增，与 scripts/git-gate.sh 同源）
# ============================================================
# 本脚本原为**私有 monorepo** 的接手自检，若干步骤以私有分区/私有本机状态为前提。
# 在公开层跑，那些步骤**必然 FAIL** ⇒ 与 git-gate.sh 同一处置：
# 判据取「私有分区目录是否存在」；不适用则**显式 SKIP**（独立第三种计分，不得写成 ok()）。
# 判据函数的四条自证与 git-gate.sh 内 `--selftest` 完全同构。
PRIVATE_DIRS="02-m1-evaluation 03-d10-workspace 04-restricted-materials"
N_PRIV=0
for d in $PRIVATE_DIRS; do [ -d "$d" ] && N_PRIV=$((N_PRIV+1)); done
if [ "$N_PRIV" -eq 0 ]; then LAYER="public"; else LAYER="private"; fi
not_applicable(){
  [ "$LAYER" = "public" ] || return 1
  [ -n "${1:-}" ] && [ -d "$1" ] && return 1
  return 0
}

# ---------- 1. 解释器 ----------
echo
echo "[1] Python 解释器"
if [ "$LAYER" = "public" ]; then
  echo " 层次: **公开层**（未检出私有分区 ⇒ 依赖私有层的步骤将 SKIP）"
else
  echo " 层次: 私有层（私有分区在场 ⇒ 全部步骤照旧）"
fi

# ---- 解释器探测（可移植；2026-09-24 公开层适配）----
# ⚠️ 原写法把本机用户名**硬编码**在解释器路径里，而公开层已把用户名脱敏为占位符 ⇒ 必然探测失败。
#    原版有「回退系统 python」的兜底，故本步尚能自愈；但**回退不可靠**
#    （系统 python 未必与托管版同版本）⇒ 统一改为**候选链探测**，与 git-gate.sh 同源。
probe_py(){
  local c
  for c in "$@"; do
    [ -n "$c" ] || continue
    if "$c" --version >/dev/null 2>&1; then printf '%s' "$c"; return 0; fi
  done
  return 1
}
CUR_VER=""
if [ -f "$HOME/.workbuddy-ai/binaries/python/versions/current" ]; then
  CUR_VER="$(tr -d ' \r\n' < "$HOME/.workbuddy-ai/binaries/python/versions/current")"
fi
PY="$(probe_py \
  "${JVS_PY:-}" \
  "$HOME/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe" \
  "$HOME/.workbuddy-ai/binaries/python/versions/3.13.12/python" \
  ${CUR_VER:+$HOME/.workbuddy-ai/binaries/python/versions/$CUR_VER/python.exe} \
  ${CUR_VER:+$HOME/.workbuddy-ai/binaries/python/versions/$CUR_VER/python} \
  "python3" "python" \
)" || PY=""
if [ -n "$PY" ]; then
  ok "Python 就绪：$("$PY" --version 2>&1 | head -1)  [$PY]"
else
  bad "未探测到可用 python（可用 JVS_PY 指定）"
fi

# ---------- 2. 关键脚本可编译 ----------
echo
echo "[2] 关键脚本语法可编译"
TOOLS="01-host-product/2026-08-28-20-59-40/tools"
# ⚠️ 用内建 `compile()` 而非 `py_compile`（2026-09-24 修，与 git-gate.sh [2] 步同源）：
#   `py_compile` 会**显式写 `.pyc`**（`-B` 抑制不了它）⇒ 自检脚本自己弄脏工作区、
#   并让文件计数失真（红线 5）。`compile()` 做同一件事但**不落盘**，强度不变。
COMPILE_CHK='import sys
src = open(sys.argv[1], encoding="utf-8").read()
compile(src, sys.argv[1], "exec")'
for f in "$TOOLS/causal/s4_pipeline.py" "$TOOLS/causal/llm_judge.py" \
         "$TOOLS/causal/relay_call.py" "$TOOLS/causal/targeted_round.py" \
         "$TOOLS/eval/eval_m1.py" "$TOOLS/host/name_scan.py"; do
  if [ -f "$f" ]; then
    if "$PY" -B -c "$COMPILE_CHK" "$f" >/dev/null 2>&1; then ok "$(basename "$f")"; else bad "$(basename "$f") 编译失败"; fi
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
# ⚠️ 公开层差异（2026-09-24）：`docs/host_memory_dump/` 是**私有语料目录**，
#   公开层不含 ⇒ 原写法产出 5 条 `[WARN] 缺失`（且**永远** WARN）。
#   永久为真的告警会训练人忽略 WARN（本项目 2026-09-21 已有同族教训）。
#   ⚠️ 反方案（已否决）：把 warn 改成不打印 —— 那会**同时掩盖私有层的真缺失**。
#   ⇒ 只做**分支**：公开层判 SKIP（显式声明「本层不含语料」），私有层判据逐字不变。
if not_applicable "02-m1-evaluation"; then
  skip "数据资产 —— 私有语料目录（docs/host_memory_dump）不随公开层发布"
else
  HMD="01-host-product/2026-08-28-20-59-40/docs/host_memory_dump"
  for f in "$HMD/all-real-events.jsonl" "$HMD/all-real-edges-final.jsonl" \
           "$HMD/causal_graph_final_v3.db" "$HMD/query-set-real-v1.jsonl" \
           "$HMD/m1_10_baseline_final/gold.jsonl"; do
    if [ -f "$f" ]; then ok "$(basename "$f")"; else warn "缺失 $(basename "$f")"; fi
  done
fi

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
# ⚠️ 公开层差异（2026-09-24）：本步检查的是「**本机** host-workspace 工作区目录的
#   Junction 是否有效」—— 那是**私有本机环境**的状态，与「本仓库是否可用」无因果关系。
#   ⚠️ 且这四条路径的 `<user>` 占位符使它在**任何**机器上都必然 `[FAIL] 4 条`
#      （没有人恰好叫 `<user>`）⇒ 4 条**永久红灯**，正是要避免的形态。
#   ⇒ 公开层判 SKIP；私有层判据逐字不变。
if not_applicable "03-d10-workspace"; then
  skip "迁移完整性 —— 检查对象是本机私有工作区的 Junction（不在仓库内）"
else
  # ⚠️ 路径一律用 `$HOME` 推导，**不得**写死任何真实用户名 ——
  #   overlay 产物是 [G] 阶段的**跳过对象**（见 jvs-build-public.py），
  #   写死的用户名**不会**被机械替换掉 ⇒ 会直接进公开仓库。
  for p in "$HOME/host-workspace/2026-08-28-20-59-40" \
           "$HOME/host-workspace/2026-09-08-20-30-19" \
           "$HOME/host-workspace/restricted-review" \
           "$HOME/host-workspace/outputs"; do
    if [ -d "$p" ]; then ok "可达 $(basename "$p")"; else bad "不可达 $p（需重建 Junction，见 MIGRATION-RECORD.md）"; fi
  done
fi

# ============================================================
# 反假绿自检：公开层却一个 SKIP 都没有 ⇒ 层次判定或跳过逻辑失灵
# ============================================================
if [ "$LAYER" = "public" ] && [ "$SKIP" -eq 0 ]; then
  echo
  echo "  [FAIL] 自检：判定为公开层却无任何 SKIP 项 —— 层次判定或跳过逻辑失灵"
  FAIL=$((FAIL+1))
fi

echo
echo "=============================================="
echo " 结果: OK=$PASS  WARN=$WARN  FAIL=$FAIL  SKIP=$SKIP"
if [ "$SKIP" -gt 0 ]; then
  echo " 说明: 有 $SKIP 项因**不适用于本层**被跳过（详见上文 [SKIP] 行）—— 非通过、非失败"
fi
if [ "$FAIL" -gt 0 ]; then
  echo " ⚠ 存在失败项，先修复再开工"
else
  echo " ✅ 环境就绪，可开工"
fi
echo "=============================================="