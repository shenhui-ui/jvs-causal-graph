#!/usr/bin/env bash
# ============================================================
# JVS 合入门禁 —— 分支合进 main 之前必须全绿
# 用法: bash scripts/git-gate.sh
#       bash scripts/git-gate.sh --selftest   # 自证：证明判据「能拒绝」
# 退出码: 0 = 可合入; 1 = 存在阻塞项
# ============================================================
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"

# ============================================================
# ⭐ 公开层适配（2026-09-24）
# ============================================================
# 背景：本门禁原为**私有 monorepo** 编写，且**硬编码了本机绝对路径**。
#   本仓库的公开层是**脱敏发布层**：真实用户名已被替换为 `<user>` 占位符
#   ⇒ 原写法（`PY` 硬编码本机绝对路径）在公开层变成 `.../Users/<user>/...`，
#     该路径**必然不存在**。
#   实测后果（2026-09-24 修前基线）：`OK=1 WARN=6 FAIL=15`，其中
#   **[2][3][4][7][10] 五步共 11 个 FAIL 全部同源于此** —— 因为五步共用 `$PY`。
#
#   ⚠️ 这是**最坏的门禁形态**：「解释器没找到」与「代码真的坏了」在输出上
#      **无法区分**。人会开始忽略 FAIL，门禁等于失效（比没有门禁更坏）。
#
# ⇒ 处置共两条，缺一不可：
#   ① **解释器改探测**（见下）—— 消除级联假 FAIL，让真故障可见；
#   ② **依赖私有层的步骤改判 SKIP**（见 `not_applicable`）—— 不假绿、不永久红灯。
# ============================================================

# ---- ① 解释器探测（可移植；不依赖任何硬编码用户名） ----
# 判据：不看可执行位，而是**实测 `--version` 能否跑通** —— 避免「文件在但跑不动」。
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
# 解释器路径可覆盖：设 JVS_PY 即优先用它；探测失败时 [1] 步报 FAIL 并列出候选顺序。

PASS=0; FAIL=0; WARN=0; SKIP=0
ok(){   echo "  [OK]   $1"; PASS=$((PASS+1)); }
bad(){  echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
warn(){ echo "  [WARN] $1"; WARN=$((WARN+1)); }
skip(){ echo "  [SKIP] $1"; SKIP=$((SKIP+1)); }

# ============================================================
# ⭐ 公开层 / 私有层自动判定（2026-09-24 新增）
# ============================================================
# 背景：本门禁原为**私有 monorepo** 编写，其相当一部分判据**天然以私有层存在为前提**
#       （语料基线、交付包指纹、`03-` 下的回归件…）。
#       在公开层里跑，这些步骤**必然 FAIL** ⇒ 结果是「要么忽略门禁、要么绕过它」，
#       两种都比没有门禁更坏。
#
# ⇒ 处置：**自动识别当前是公开层**，并把「依赖私有层」的步骤改为**显式 SKIP**。
#
# ⚠️ 设计纪律（**不得退化为「跳过即放行」**）：
#   1. `[SKIP]` 是**独立的第三种计分**，既不进 PASS 也不进 FAIL —— 不得写成 `ok()`。
#   2. 判据取「**私有分区目录是否存在**」，而不是「某个文件在不在」——
#      后者会被单点缺失误判成公开层。
#   3. 若**判定为私有层**，所有适用步骤照旧全跑（本文件对私有层的语义**零改动**）。
#   4. 跳过项目**必须在汇总行单独列出条数**，让人一眼看到「有几项没跑」。
PRIVATE_DIRS="02-m1-evaluation 03-d10-workspace 04-restricted-materials"
N_PRIV=0
for d in $PRIVATE_DIRS; do [ -d "$d" ] && N_PRIV=$((N_PRIV+1)); done
if [ "$N_PRIV" -eq 0 ]; then
  LAYER="public"
else
  LAYER="private"
fi

# 判据函数：**当前步骤是否不适用**（返回 0 = 不适用/应跳过；1 = 适用/照旧跑）。
# ⚠️ 三条约束（均由 `--selftest` 逐条自证，勿改）：
#   - 公开层 **且** 私有标识路径不在场 ⇒ 不适用；否则**一律适用**。
#   - 公开层下私有标识**在场**时仍判「适用」——防止跳过条件过宽而假绿。
#   - 私有层下**一律适用** —— 保证私有层语义与改动前逐字节等价。
not_applicable(){
  [ "$LAYER" = "public" ] || return 1
  [ -n "${1:-}" ] && [ -d "$1" ] && return 1
  return 0
}

# ---- ② 自证（红线 10：校验要「能拒绝」才算校验） ----
if [ "${1:-}" = "--selftest" ]; then
  echo "=============================================="
  echo " [SELFTEST] 公开层适配判据自证"
  echo "=============================================="
  rc_st=0
  # ⚠️ 夹具纪律：P1 需要「一个**真实存在**的目录」。**不得**直接借用 `02-m1-evaluation`
  #    —— 本脚本的 cwd 是 `dirname($0)/..`，在公开层里那个目录**必然不存在**，
  #    于是 P1 会因「夹具不在场」而假失败（2026-09-24 实测踩到）。
  #    ⇒ 用 `mktemp -d` 造一个与 cwd 无关的真实目录，用完即删（不触碰任何生产路径）。
  _st_probe="$(mktemp -d 2>/dev/null || true)"
  if [ -n "$_st_probe" ] && [ -d "$_st_probe" ]; then
    LAYER="public"
    if not_applicable "$_st_probe"; then
      echo "  [FAIL] P1 公开层 + 私有标识在场 ⇒ 却判「不适用」（跳过条件过宽，会假绿）"; rc_st=1
    else
      echo "  [OK]   P1 公开层 + 私有标识在场 ⇒ 判「适用」（不误跳过）"
    fi
    rmdir "$_st_probe" 2>/dev/null || true
  else
    echo "  [FAIL] P1 夹具创建失败（mktemp -d 不可用）—— 本项未验证，不得当通过"; rc_st=1
  fi
  if not_applicable "__definitely_absent__"; then
    echo "  [OK]   P2 公开层 + 私有标识不在场 ⇒ 判「不适用」（可跳过）"
  else
    echo "  [FAIL] P2 公开层 + 私有标识不在场 ⇒ 仍判「适用」（跳过条件失效，会永久红灯）"; rc_st=1
  fi
  LAYER="private"
  if not_applicable "__definitely_absent__"; then
    echo "  [FAIL] P3 私有层 ⇒ 却判「不适用」（私有层语义被改动）"; rc_st=1
  else
    echo "  [OK]   P3 私有层 ⇒ 一律判「适用」（私有层语义零改动）"
  fi
  LAYER="public"
  SKIP=0; skip "探针（自证用，不计入正式结果）" >/dev/null
  if [ "$SKIP" -eq 1 ]; then
    echo "  [OK]   P4 skip() 真实计入 SKIP（第三种计分生效，跳过项不会静默消失）"
  else
    echo "  [FAIL] P4 skip() 未计入 SKIP（跳过项会静默消失）"; rc_st=1
  fi
  echo "----------------------------------------------"
  if [ "$rc_st" -eq 0 ]; then
    echo " 自检通过：4/4 条判据均「能拒绝、不误报」"
  else
    echo " 自检**未通过** —— 公开层适配不可信，禁止据此放行"
  fi
  echo "=============================================="
  exit "$rc_st"
fi

echo "=============================================="
echo " JVS 合入门禁  ($(date '+%Y-%m-%d %H:%M'))"
echo " 仓库: $ROOT"
if [ "$LAYER" = "public" ]; then
  echo " 层次: **公开层**（未检出私有分区 ⇒ 依赖私有层的步骤将 SKIP）"
else
  echo " 层次: 私有层（私有分区在场 ⇒ 全部步骤照旧）"
fi
echo "=============================================="

# ---------- 1. 解释器 ----------
echo
echo "[1] Python 解释器"
if [ -n "$PY" ]; then ok "$("$PY" --version 2>&1 | head -1)  [$PY]"
else bad "未探测到可用 Python 解释器 —— 后续依赖它的步骤将同步失败（可用 JVS_PY 指定）"; fi

# ---------- 2. 关键脚本可编译 ----------
echo
echo "[2] 关键脚本语法"
# ⚠️ 判据实现纪律（2026-09-24 修，勿回退）：
#   原写法 `"$PY" -B -m py_compile "$f"` —— **`-B` 在这里不起作用**。
#   `-B` 只抑制「import 时的字节码缓存」，而 `py_compile` 模块是**显式写 `.pyc`**
#   ⇒ 门禁每跑一次就产出 6 个 `__pycache__/*.pyc`。实测后果：
#     ① 工作区被门禁自己弄脏（[9] 步随即 WARN「未跟踪项」）；
#     ② **文件计数失真**：构建报 179，核验器扫到 **185**（差 6 = 6 个 pyc）
#        —— 正是红线 5「计数类断言会静默过期」。
#   ⇒ 改用**内建 `compile()`**：与 `py_compile` 做**同一件事**（都是编译到字节码、
#      都会抛 SyntaxError），但**完全不落盘**。语法检查的强度**未降低**。
#      （`py_compile.compile()` 内部就是调 `compile()`，故二者等价。）
#   自证：负向自证夹具 T11 —— 跑完门禁后**不得新增任何 `__pycache__`**。
COMPILE_CHK='import sys
src = open(sys.argv[1], encoding="utf-8").read()
compile(src, sys.argv[1], "exec")'
TOOLS="01-host-product/2026-08-28-20-59-40/tools"
for f in "$TOOLS/causal/s4_pipeline.py" "$TOOLS/causal/llm_judge.py" \
         "$TOOLS/causal/relay_call.py" "$TOOLS/causal/targeted_round.py" \
         "$TOOLS/causal/causal_graph.py" "$TOOLS/host/name_scan.py"; do
  if [ -f "$f" ]; then
    if "$PY" -B -c "$COMPILE_CHK" "$f" >/dev/null 2>&1; then ok "$(basename "$f")"
    else bad "$(basename "$f") 编译失败"; fi
  else warn "缺失 $f"; fi
done

# ---------- 3. 离线回归 ----------
echo
echo "[3] 离线回归（不调模型）"
if [ -f "$TOOLS/causal/test_llm_judge.py" ]; then
  out=$(cd "$TOOLS/causal" && "$PY" -B test_llm_judge.py 2>&1 | tail -3)
  if echo "$out" | grep -qiE "^(OK|Ran [0-9]+ test)"; then ok "test_llm_judge 通过"
  else bad "test_llm_judge 未通过: $(echo "$out" | head -1)"; fi
else warn "未找到 test_llm_judge.py"; fi
# 因果层入库闸门回归（conf<=0.6 改标 pending 而非 skip）。判据:必须打印 OK 结尾,
# 且**不得**出现 FAILED —— 只看 "Ran N test" 会把失败也当成通过。
if [ -f "$TOOLS/causal/test_causal_graph.py" ]; then
  out=$(cd "$TOOLS/causal" && "$PY" -B test_causal_graph.py 2>&1)
  last=$(echo "$out" | tail -1)
  if echo "$out" | grep -q "FAILED"; then
    bad "test_causal_graph 失败: $(echo "$out" | grep -m1 -A2 'FAIL:' | head -1)"
  elif [ "$last" = "OK" ]; then ok "test_causal_graph 通过"
  else bad "test_causal_graph 未通过: $last"; fi
else warn "未找到 test_causal_graph.py"; fi

# 提示词渲染契约回归（strength 被 build_prompt 逐字渲染进 prompt）。
# 拦的是 D-① 闸门改标 pending 后「weak 边的『/弱』语义信号消失」（实测 7/10 题退化）。
if [ -f "$TOOLS/causal/test_llm_answer_render.py" ]; then
  out=$(cd "$TOOLS/causal" && "$PY" -B test_llm_answer_render.py 2>&1)
  last=$(echo "$out" | tail -1)
  if echo "$out" | grep -q "FAILED"; then
    bad "test_llm_answer_render 失败: $(echo "$out" | grep -m1 -A2 'FAIL:' | head -1)"
  elif [ "$last" = "OK" ]; then ok "test_llm_answer_render 通过"
  else bad "test_llm_answer_render 未通过: $last"; fi
else warn "未找到 test_llm_answer_render.py"; fi

# ---------- 4. 冻结产物指纹 ----------
echo
echo "[4] 冻结产物 SHA256 与基线一致"
# ⚠️ 公开层差异（2026-09-24）：原清单 13 项中含 8 项**私有层**产物（语料 / 交付包），
#   公开层不存在这些文件 ⇒ 逐项 `[缺失]` 后判 FAIL，属**结构性误报**。
#   公开层的 `scripts/frozen-fingerprints.json` 只列 5 项**公开层内可核验**的产物。
#   ⚠️ 本步**不判 SKIP** —— 它是**完整可核验**的（判据源在、被判对象全在），
#      只是**规模**从 13 项缩到 5 项。「按公开层规模核验」≠「跳过」；
#      写成 SKIP 会虚增跳过数、淹没真正没跑的步骤（同日实测踩到并修正）。
#   判据、输出纪律、自证方式与私有层**逐字相同**（未放宽任何阈值）。
if [ "$LAYER" = "public" ]; then
  FP="scripts/frozen-fingerprints.json"
  if [ -f "$FP" ]; then
    "$PY" -B - "$FP" <<'PYEOF'
import json, os, sys, hashlib
fp = json.load(open(sys.argv[1], encoding="utf-8"))
bad = miss = good = 0
for it in fp["items"]:
    p = it["path"].replace("/", os.sep)
    if not os.path.exists(p):
        print(f"          [缺失] {it['label']}  ({it['path']})"); miss += 1; continue
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    if h != it["sha256"]:
        print(f"          [不符] {it['label']}")
        print(f"                 基线 {it['sha256'][:32]}")
        print(f"                 当前 {h[:32]}")
        bad += 1
    else:
        good += 1
print(f"          指纹一致 {good}/{len(fp['items'])}")
if bad or miss:
    print(f"  => 异常 {bad} 项不符, {miss} 项缺失")
    sys.exit(1)
PYEOF
    if [ $? -eq 0 ]; then ok "公开层冻结产物指纹全部一致（公开层清单 5 项）"
    else bad "公开层冻结产物指纹存在偏差 —— 若为有意改动，须更新 $FP"; fi
  else
    bad "未找到 $FP —— 判据源缺失，无法核验冻结产物（不得以此放行）"
  fi
else
  # ---------- 私有层：原判据逐字保留 ----------
  # ⚠️ 输出纪律（2026-09-21 修，勿回退）：
  #   `[OK]` / `[FAIL]` / `[WARN]` 是 bash 侧 `ok()` / `bad()` / `warn()` 的**专属标记**，各计 1 分。
  #   ⇒ **内嵌 Python 片段不得打印这三个前缀**，只打印缩进明细；失败用 `[缺失]` / `[不符]` 独立 token。
  #   反面教材：原 `print(f"  [OK]   指纹一致 N/M")` 不走 `ok()`，
  #   造成「**打印 16 行 / 计分 15**」—— 数打印行就会把文档里的基线写成 16（静默过期，见 GIT-POLICY §五）。
  #   自证：`grep -cE '\[OK\]' <门禁输出>` 必须 == 脚本自报的 `OK` 数。
  FP="scripts/frozen-fingerprints.json"
  if [ -f "$FP" ]; then
    "$PY" -B - "$FP" <<'PYEOF'
import json, os, sys, hashlib
fp = json.load(open(sys.argv[1], encoding="utf-8"))
bad = miss = good = 0
for it in fp["items"]:
    p = it["path"].replace("/", os.sep)
    if not os.path.exists(p):
        print(f"          [缺失] {it['label']}  ({it['path']})"); miss += 1; continue
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    if h != it["sha256"]:
        print(f"          [不符] {it['label']}")
        print(f"                 基线 {it['sha256'][:32]}")
        print(f"                 当前 {h[:32]}")
        bad += 1
    else:
        good += 1
print(f"          指纹一致 {good}/{len(fp['items'])}")
if bad or miss:
    print(f"  => 异常 {bad} 项不符, {miss} 项缺失")
    sys.exit(1)
PYEOF
    if [ $? -eq 0 ]; then ok "全部冻结产物指纹一致"
    else bad "冻结产物指纹存在偏差 —— 若为有意改动，须更新 $FP"; fi
  else
    warn "未找到 $FP，跳过指纹校验"
  fi
fi

# ---------- 5. 核心大体积产物仍在（基线驱动，精确判据） ----------
echo
echo "[5] 核心产物未被误排除（语料路径基线）"
# 演进史（勿回退）：
#   原实现 = 两次全树 `find`（.sse / .zip）+ 阈值 `-ge 2000` / `-ge 30`。
#   两个缺陷：① 慢（本机实测两次合计 2,727–2,946 ms）；
#             ② **弱** —— 负向夹具实测「2,364 → 2,363」时阈值仍全绿放行，
#                即**丢 363 个 `.sse` 仍允许合入**，而这正是 GIT-POLICY §11 铁律 1
#                （「SIGTERM 把工作区文件批量移入回收站」）要拦的事故形态。
#   现实现 = 读 `scripts/corpus-baseline.jsonl`（**只增不减**的路径清单）+ 逐条 `os.stat`，
#            **精确判据 + 能指名缺失文件 + 阻塞**。实测端到端 930 ms（含解释器启动）。
#   ⚠️ 为什么不用 `_index/文件索引.jsonl` 当清单：「索引与 HEAD 一致」≠「索引新鲜」——
#      若**先删盘、再刷新索引**，路径已从索引消失 ⇒ stat 无事可做 ⇒ 静默漏报。
#      基线才是真值源，且**绝不随索引刷新而缩小**（详见 PHASE-C-PLAN.md §5.4 问题 1）。
#   ⚠️ 为什么门禁里不做哈希：全量 sha256 约 5.5 s（3.5 GB），会抵掉本步收益。
#      哈希判据走 `--hashes` 手动通道；门禁只判「路径在位」（= 原 `find` 的同级语义）。
#   证据（负向夹具四测 A/B/C/D 全过 + 同轮 A/B 计时）：`_staging/tools/phase-c4-fixture.py`
#
# ⚠️ 公开层差异（2026-09-24）：本步判据源 `scripts/corpus-baseline.jsonl` 记录的是
#   **私有语料（.sse / .zip）的路径清单**，该清单本身**已从公开层移除**（属私有结构暴露）
#   ⇒ 公开层**既没有判据源，也没有被判对象** ⇒ 本步对公开层**结构性不适用**，判 SKIP。
#   ⚠️ **不得**简化为「文件不在就放行」：故此处用 `not_applicable`（只看私有分区目录），
#      而不是 `if [ -f "$B_CORPUS" ]`（那会把「单点删除判据源」也变成放行 ⇒ 假绿）。
B_CORPUS="scripts/corpus-baseline.jsonl"
if not_applicable "02-m1-evaluation"; then
  skip "语料路径基线 —— 公开层不含私有语料，判据源与被判对象均不在场（非放行）"
elif [ -f "$B_CORPUS" ]; then
  out_corpus=$("$PY" -B scripts/corpus-baseline.py check --root . 2>&1)
  if [ $? -eq 0 ]; then
    ok "$(echo "$out_corpus" | sed -n 's/.*\(语料基线 [0-9]* 条.*缺失 0\).*/\1/p')"
  else
    bad "语料基线存在缺失 —— 禁止合入 main"
    echo "$out_corpus" | sed 's/^/         /'
  fi
else
  bad "未找到 $B_CORPUS —— 判据源缺失，无法验证核心产物在位（不得以此放行）"
fi

# ---------- 6. 凭证扫描 ----------
echo
echo "[6] 凭证扫描（阻塞项）"
if [ -f "scripts/scan-secrets.sh" ]; then
  if bash scripts/scan-secrets.sh >/dev/null 2>&1; then ok "无凭证泄漏"
  else bad "发现疑似凭证 —— 禁止合入，详见 bash scripts/scan-secrets.sh"; fi
else warn "未找到 scripts/scan-secrets.sh"; fi

# ---------- 7. JSON 产物可解析 ----------
echo
echo "[7] 核心 JSON 可解析"
# ⚠️ 公开层差异（2026-09-24）：验收台账（`acceptance-ledger.json`）属 D10 私有交付包，
#   公开层不含 ⇒ 原写法会产出 1 条误导性 `[WARN] 缺失`（"缺失" 暗示被误删，
#   而事实是**本就不属于本层**）⇒ 公开层清单只列本层实际拥有的 JSON。
#   私有层清单与判据**逐字不变**。
JSON_LIST="scripts/frozen-fingerprints.json"
if [ "$LAYER" = "private" ]; then
  JSON_LIST="03-d10-workspace/2026-09-08-20-30-19/outputs/full-review-full-20260909/acceptance-ledger.json
scripts/frozen-fingerprints.json"
fi
if [ -z "$JSON_LIST" ]; then bad "JSON 清单为空 —— 该步等于没跑（反假绿守卫）"; fi
for f in $JSON_LIST; do
  if [ -f "$f" ]; then
    if "$PY" -B -c "import json,sys; json.load(open(sys.argv[1],encoding='utf-8'))" "$f" >/dev/null 2>&1
    then ok "$(basename "$f")"
    else bad "$(basename "$f") 解析失败"; fi
  else warn "缺失 $f"; fi
done

# ---------- 8. 复核机械回归（非阻塞；清单为空除外） ----------
echo
echo "[8] 复核机械回归（非阻塞；清单为空除外）"
# 说明：本步原为硬编码 warn「test_review_scoped_validator.py 预期 2 个 FAIL」。
# 该告警自 d381b38（2026-09-16）修复后即已失效 —— 永久为真的告警会训练人忽略 WARN。
# 改为真跑测试：通过则 OK，不通过才 WARN。
# 2026-09-21（用户授权）：由「硬编码单文件」改为**清单驱动**，纳入 C-2 前置新补的两件回归件。
#   判据与旧版逐字一致 —— 末行 ^OK$ 即通过；文件缺失仍只 WARN（本步「非阻塞」的语义不变）。
#   ⚠️ 新增回归件时**只在本列表追加一行**，不要复制粘贴整段。
#   ⚠️ 不得改用 `echo "$LIST" | while read ...` —— 管道会让循环跑在**子 shell** 里，
#      ok/warn 递增的是子 shell 的计数器 ⇒ 汇总行与「自报数 == [OK] 行数」双双失真。
#
# ⚠️ 公开层差异（2026-09-24）：三件回归件全部位于私有分区 `03-d10-workspace/` 下
#   ⇒ 公开层里原写法产出 3 条 `[WARN] 未找到`（且**永远** WARN ⇒ 训练人忽略 WARN）。
#   ⚠️ 注意本步自身有「清单为空 ⇒ bad」的反假绿守卫（`REG_RAN`）—— 直接过滤清单会撞上它。
#   ⇒ 处置：公开层下**本步整体判 SKIP 并跳过循环**，不制造 3 条必为 WARN 的噪声；
#      该守卫仅在私有层生效（私有层清单非空，守卫语义不变）。
if not_applicable "03-d10-workspace"; then
  skip "复核机械回归 —— 三件回归件均属私有分区（本步非阻塞步骤，跳过不影响合入判据）"
else
  REG_TESTS="03-d10-workspace/2026-09-08-20-30-19/test_review_scoped_validator.py
03-d10-workspace/2026-09-08-20-30-19/test_review_full_validator.py
03-d10-workspace/2026-09-08-20-30-19/outputs/test_verify_delivery.py"
  REG_RAN=0
  for T_RSC in $REG_TESTS; do
    REG_RAN=$((REG_RAN+1))
    T_NAME="$(basename "$T_RSC" .py)"
    if [ ! -f "$T_RSC" ]; then warn "未找到 $T_RSC"; continue; fi
    out_rsc=$(cd "$(dirname "$T_RSC")" && "$PY" -B "$(basename "$T_RSC")" 2>&1)
    if echo "$out_rsc" | grep -qE "^OK$"; then ok "$T_NAME 通过"
    else
      warn "$T_NAME 未全通过（非阻塞，仅提示）"
      echo "$out_rsc" | tail -6 | sed 's/^/         /'
    fi
  done
  if [ "$REG_RAN" -eq 0 ]; then bad "复核机械回归清单为空 —— 该步等于没跑（反假绿守卫）"; fi
fi

# ---------- 9. 工作区清洁度 ----------
echo
echo "[9] 工作区状态"
if git rev-parse --git-dir >/dev/null 2>&1; then
  BR=$(git rev-parse --abbrev-ref HEAD)
  DIRTY=$(git status --porcelain | wc -l | tr -d ' ')
  UNTR=$(git status --porcelain | grep -c '^??' || true)
  echo "         当前分支: $BR"
  if [ "$UNTR" -gt 0 ]; then warn "有 $UNTR 个未跟踪项（确认是否为预期新文件）"
  else ok "无未跟踪项"; fi
  if [ "$DIRTY" -eq 0 ]; then ok "工作区干净"
  else warn "有 $DIRTY 项未提交变更"; fi
  # ⭐ 红线 1 守卫：处于 main 时，末提交必须是 `--no-ff` 双父合并（父数 3）。
  # 为什么需要这条：pre-commit 钩子是「提交时」的守卫，本步是「合入前 / 在 main 上收尾时」
  # 的另一道守卫，两者互为补充（钩子可被 --no-verify 绕过，本步不会）。
  # 事故实证 2026-09-23：一次 `git commit` 直提 main 产生**单父**提交 20ce9fe
  # （父数 2），违反红线 1 与 DEFINITION-OF-DONE §6 第三条；已以 plumbing 回退
  # main 并用 `--no-ff` 重建双父合并（af66cc1），违规提交保留在历史中可追溯。
  # 判据取 `< 3` 而非 `!= 3`：同时覆盖「单父(=2)」与「无父(=1，初始提交）」两种非合并形态。
  #
  # ⚠️ 公开层差异（2026-09-24）：本守卫的两条前提在公开层**均不成立** ——
  #   ① 它拦的是「**多分支工作流**里直提 main」；公开层是**单线线性历史**发布仓库
  #      （`git log` 两步：初始提交 → 修正提交），不存在需要 `--no-ff` 的合并场景；
  #   ② 它拦的是「**私有 monorepo 误把大体积语料提交进 main**」这一事故形态，
  #      而公开层不含语料 ⇒ 该事故形态在本层不可发生。
  #   ⇒ **不适用**，判 SKIP。
  #   ⚠️ 反方案（已否决）：把判据放宽为 `>= 2`（允许单父）—— 那会**同时放行私有层
  #      的违规单父提交**（把真守卫一起废掉），属「为了让公开层变绿而削弱私有层」，
  #      是比红灯更坏的处置。故只做**分支**，不做**放宽**。
  if [ "$BR" = "main" ]; then
    if not_applicable "03-d10-workspace"; then
      skip "main 末提交父数 —— 公开层为单线线性历史（无可合并的分支，见上注）"
    else
      NPAR=$(git rev-list --parents -n 1 HEAD | wc -w | tr -d ' ')
      if [ "$NPAR" -lt 3 ]; then
        bad "当前在 main 上，末提交父数=$NPAR（须 3 = 双父合并）—— 疑似直提 main（红线 1）或合并退化为单父"
      else
        ok "在 main 上且末提交为双父合并（父数 3）"
      fi
    fi
  fi
else
  warn "当前目录不是 git 仓库（首次初始化时属正常）"
fi

# ---------- 10. 技能层机械校验 ----------
echo
echo "[10] 技能层机械校验（双副本 / 完整性三关 / 数量断言）"
# 判据源: scripts/skills-gate.py（P1 于 2026-09-21 落库；P1b 于同日接线进本步）
# ⚠️ 输出纪律（与 [4] 步同源，勿回退）：
#   `[OK]` / `[FAIL]` / `[WARN]` 是 bash 侧 ok() / bad() / warn() 的**专属标记**，各计 1 分。
#   skills-gate.py 的标记是 `[通过]` / `[不通过]` / `[提示]` / `[自检]`（见其 docstring），
#   **刻意避开**这三个 token ⇒ 不得把它的 stdout 直接混进门禁输出。
#   ⚠️ 本步回显前**再中和一次** `[OK]`：它的 `[提示]` / `[不通过]` 文本可能内嵌 **README 原文**，
#      而 README 是人工写的 ⇒ 未来可能含 `[OK]`。否则重演 [4] 步反面教材「打印行数 != 计分行数」。
#      （同族陷阱：`grep -cF '[通过]'` 会把 `[自检]` 行自己数成 +1 —— 故**不得**拿 `[通过]` 计数。）
# ⚠️ 降级口径（2026-09-21 用户裁定 = **一律阻塞，不设豁免开关**）：
#   用户级技能根（`~/.workbuddy-ai/skills`）缺失 ⇒ 判 bad。
#   理由：本脚本第 10 行已**硬编码本机解释器绝对路径** ⇒ 它本就是**本机专属**门禁，
#         而用户级根与之同处 `C:/Users/<user>/.workbuddy-ai/` 下 ⇒ 缺失属**环境故障**，
#         不是「可移植性条件」（换机器会先在 [1] 步红）。与 [5] 步「判据源缺失 ⇒ 不得以此放行」同构。
#   负向夹具（不触碰生产）：`JVS_SKILLS_USER_ROOT=<不存在的路径> bash scripts/git-gate.sh` ⇒ 本步必 bad。
#
# ⚠️ 公开层差异（2026-09-24）：被校验的**技能层本体**是用户级目录 `~/.workbuddy-ai/skills`
#   （**仓库之外**），其内容由**提交者本机**决定 ⇒ 公开层克隆者跑本步会在校验
#   **第三方的个人环境**，与「本仓库是否可合入」无因果关系 ⇒ **不适用**，判 SKIP。
#   ⚠️ 上条注释里「硬编码本机路径 ⇒ 本机专属门禁」的论证已随解释器探测改动而**部分失效**
#      （2026-09-24）：本脚本现已可移植 ⇒ 不再是「本机专属」。但 SKIP 结论**不依赖**该论证，
#      而依赖「校验对象在仓库之外」这一独立理由 —— 故结论不变。
#   ⚠️ 公开层的 `scripts/skills-gate.py` **仍随仓库发布**（可手动运行、可读、可审计），
#      只是**不进门禁判据** —— 这是「保留能力」与「不误判合入条件」的分离。
if not_applicable "03-d10-workspace"; then
  skip "技能层校验 —— 校验对象在仓库之外（用户级 ~/.workbuddy-ai/skills），与可合入性无因果"
else
  SG="scripts/skills-gate.py"
  if [ ! -f "$SG" ]; then
    bad "未找到 $SG —— 判据源缺失，无法校验技能层（不得以此放行）"
  else
    out_sg=$("$PY" -B "$SG" --quiet 2>&1); rc_sg=$?
    # 「判据确实跑了」：解析不到汇总行 ⇒ 脚本输出格式已变 ⇒ 接线判据失配，**不得当通过**
    n_pass=$(printf '%s\n' "$out_sg" | sed -n 's/^ 结果: 通过=\([0-9][0-9]*\).*/\1/p' | head -1)
    n_fail=$(printf '%s\n' "$out_sg" | sed -n 's/^ 结果: 通过=[0-9][0-9]* *不通过=\([0-9][0-9]*\).*/\1/p' | head -1)
    if [ -z "$n_pass" ] || [ -z "$n_fail" ]; then
      bad "技能层校验未产出可解析的汇总行（rc=$rc_sg）—— 接线判据失配或解释器不可用"
      printf '%s\n' "$out_sg" | sed 's/\[OK\]/(OK)/g' | sed 's/^/         /'
    elif [ "$rc_sg" -eq 0 ]; then
      ok "技能层机械校验全过（通过 $n_pass 项 / 不通过 0）"
    elif [ "$n_fail" -eq 0 ]; then
      bad "技能层机械校验自检不一致（不通过 0 但 rc=$rc_sg）—— 脚本自身判定不可信，详见 python $SG"
      printf '%s\n' "$out_sg" | sed -n '/^ \[自检\]/p' | sed 's/\[OK\]/(OK)/g' | sed 's/^/         /'
    else
      n_bad_line=$(printf '%s\n' "$out_sg" | grep -cF '[不通过]')
      bad "技能层机械校验有 $n_fail 项阻塞 —— 详见 python $SG"
      printf '%s\n' "$out_sg" | grep -F '[不通过]' | head -12 | sed 's/\[OK\]/(OK)/g' | sed 's/^/         /'
      if [ "$n_bad_line" -gt 12 ]; then
        echo "         ...（另有 $((n_bad_line - 12)) 行，详见 python $SG）"
      fi
    fi
  fi
fi

# ============================================================
# 反假绿自检：公开层却一个 SKIP 都没有 ⇒ 层次判定失灵（跳过逻辑没生效）
# ============================================================
if [ "$LAYER" = "public" ] && [ "$SKIP" -eq 0 ]; then
  echo
  echo "  [FAIL] 自检：判定为公开层却无任何 SKIP 项 —— 层次判定或跳过逻辑失灵"
  FAIL=$((FAIL+1))
fi

echo
echo "=============================================="
# ⚠️ 汇总行**必须单列 SKIP**（设计纪律第 4 条）：只报 OK/WARN/FAIL 会让「有几项没跑」
#    完全不可见 ⇒ 读者会把「OK=14 / FAIL=0」误读成「14 项全查过了」。
echo " 结果: OK=$PASS  WARN=$WARN  FAIL=$FAIL  SKIP=$SKIP"
if [ "$SKIP" -gt 0 ]; then
  echo " 说明: 有 $SKIP 项因**不适用于本层**被跳过（详见上文 [SKIP] 行）—— 非通过、非失败"
fi
if [ "$FAIL" -gt 0 ]; then
  echo " 禁止合入 main —— 先修复 FAIL 项"
  echo "=============================================="
  exit 1
else
  echo " 门禁通过，允许合入 main"
  echo "=============================================="
  exit 0
fi