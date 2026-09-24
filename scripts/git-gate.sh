#!/usr/bin/env bash
# ============================================================
# JVS 合入门禁 —— 分支合进 main 之前必须全绿
# 用法: bash scripts/git-gate.sh
# 退出码: 0 = 可合入; 1 = 存在阻塞项
# ============================================================
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
# 解释器路径可覆盖：设 JVS_PY 即换机可用；不设则与既有硬编码值逐字节相同。
PY="${JVS_PY:-C:/Users/<user>/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe}"

echo "=============================================="
echo " JVS 合入门禁  ($(date '+%Y-%m-%d %H:%M'))"
echo " 仓库: $ROOT"
echo "=============================================="

PASS=0; FAIL=0; WARN=0
ok(){   echo "  [OK]   $1"; PASS=$((PASS+1)); }
bad(){  echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
warn(){ echo "  [WARN] $1"; WARN=$((WARN+1)); }

# ---------- 1. 解释器 ----------
echo
echo "[1] Python 解释器"
if [ -x "$PY" ]; then ok "$("$PY" --version 2>&1)"; else bad "托管解释器不可用: $PY"; fi

# ---------- 2. 关键脚本可编译 ----------
echo
echo "[2] 关键脚本语法"
TOOLS="01-host-product/2026-08-28-20-59-40/tools"
for f in "$TOOLS/causal/s4_pipeline.py" "$TOOLS/causal/llm_judge.py" \
         "$TOOLS/causal/relay_call.py" "$TOOLS/causal/targeted_round.py" \
         "$TOOLS/causal/causal_graph.py" "$TOOLS/host/name_scan.py"; do
  if [ -f "$f" ]; then
    if "$PY" -B -m py_compile "$f" >/dev/null 2>&1; then ok "$(basename "$f")"
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
B_CORPUS="scripts/corpus-baseline.jsonl"
if [ -f "$B_CORPUS" ]; then
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
for f in "03-d10-workspace/2026-09-08-20-30-19/outputs/full-review-full-20260909/acceptance-ledger.json" \
         "scripts/frozen-fingerprints.json"; do
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
  if [ "$BR" = "main" ]; then
    NPAR=$(git rev-list --parents -n 1 HEAD | wc -w | tr -d ' ')
    if [ "$NPAR" -lt 3 ]; then
      bad "当前在 main 上，末提交父数=$NPAR（须 3 = 双父合并）—— 疑似直提 main（红线 1）或合并退化为单父"
    else
      ok "在 main 上且末提交为双父合并（父数 3）"
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

echo
echo "=============================================="
echo " 结果: OK=$PASS  WARN=$WARN  FAIL=$FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo " 禁止合入 main —— 先修复 FAIL 项"
  echo "=============================================="
  exit 1
else
  echo " 门禁通过，允许合入 main"
  echo "=============================================="
  exit 0
fi