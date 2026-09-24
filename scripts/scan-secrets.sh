#!/usr/bin/env bash
# ============================================================
# JVS 凭证扫描 —— 阻塞式，命中即禁止合入
# 用法: bash scripts/scan-secrets.sh
# 退出码: 0 = 干净; 1 = 发现疑似凭证
# ============================================================
set -u
cd "$(dirname "$0")/.." || exit 1
# 解释器路径可覆盖：设 JVS_PY 即换机可用；不设则与既有硬编码值逐字节相同。
PY="${JVS_PY:-C:/Users/<user>/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe}"
HITS=0

echo "------------- 凭证扫描 -------------"

# ---------- 1. 凭证类文件 ----------
# 注意：磁盘上存在凭证文件是正常的（.gitignore 只排除、不删除）。
# 真正阻塞的是「凭证文件被 git 追踪」，即进了版本库。
echo "[1] 凭证类文件是否被版本库追踪"
if git rev-parse --git-dir >/dev/null 2>&1; then
  TRACKED_CRED=$(git ls-files | grep -iE '(\.doubao_session\.json|^\.env|/\.env$|\.env\.|\.pem$|\.key$|\.p12$|\.pfx$|credentials.*\.json$|secrets.*\.json$|id_rsa|id_ed25519|\.netrc$)' || true)
  if [ -n "$TRACKED_CRED" ]; then
    echo "$TRACKED_CRED" | while read -r f; do echo "  [FAIL] 已被追踪: $f"; done
    HITS=$((HITS+1))
  else
    echo "  无（磁盘上可能存在凭证文件，但均未被追踪）"
  fi
else
  echo "  (尚未初始化仓库，跳过)"
fi

# ---------- 2. 内容模式扫描 ----------
echo "[2] 内容模式（sk- / ghp_ / AKIA / AIza / 赋值式密钥）"
if [ -x "$PY" ]; then
  "$PY" -B - <<'PYEOF'
import os, re, sys
PATS = [
    ("sk-",     re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
    ("ghp_",    re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("AKIA",    re.compile(r"AKIA[0-9A-Z]{16}")),
    ("AIza",    re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("assign",  re.compile(r"(?i)(api[_-]?key|secret|passwd|password|token)\s*[:=]\s*[\"'][^\"'\s]{20,}[\"']")),
]
SKIP_DIR = {".git", "__pycache__", "node_modules", "04-restricted-materials"}
SKIP_EXT = {".sse", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".db", ".sqlite",
            ".pyc", ".ico", ".woff", ".woff2", ".pdf", ".xlsx", ".docx"}
# 扫描器自身与治理文档含模式字面量，不参与扫描
# ⚠️ 口径必须与 .githooks/pre-commit 的 SKIP_FILE 逐项一致（2026-09-21 修正）。
# ⚠️ 2026-09-23 补入 `pre-commit`：该文件含与本文件 :37 同形的模式字面量
#    （`sk-[A-Za-z0-9_\-]{20,}`），属「扫描器自身」同一组。
#    不是放宽防护 —— 跳过的仍是同一组文件、同一原因。
SKIP_FILE = {"scan-secrets.sh", "git-gate.sh", "pre-commit"}
# 已确认的测试假数据（合成夹具，非真实凭证）
ALLOWLIST = [
    # 该测试夹具已在公开版本中改为运行时拼接，源码内不含类密钥字面量
    ("test_targeted_round.py", "运行时拼接"),
]
hits = 0
for root, dirs, files in os.walk("."):
    dirs[:] = [d for d in dirs if d not in SKIP_DIR]
    for f in files:
        if f in SKIP_FILE:
            continue
        if os.path.splitext(f)[1].lower() in SKIP_EXT:
            continue
        p = os.path.join(root, f)
        try:
            if os.path.getsize(p) > 4 * 1024 * 1024:
                continue
            txt = open(p, "r", encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for name, rx in PATS:
            for m in rx.finditer(txt):
                s = m.group(0)
                if any((k in p) and (allowed in s) for k, allowed in ALLOWLIST):
                    continue
                print(f"  命中 [{name}] {p}")
                print(f"        {s[:70]}")
                hits += 1
if hits == 0:
    print("  无")
else:
    print(f"  合计 {hits} 处")
sys.exit(1 if hits else 0)
PYEOF
  [ $? -ne 0 ] && HITS=$((HITS+1))
else
  echo "  (解释器不可用，跳过内容扫描)"
fi

# ---------- 3. 受限素材不得进入版本库 ----------
echo "[3] 受限素材不得入库"
if git rev-parse --git-dir >/dev/null 2>&1; then
  TRACKED=$(git ls-files | grep -c '^04-restricted-materials/' || true)
  if [ "$TRACKED" -gt 0 ]; then
    echo "  [FAIL] 有 $TRACKED 个受限素材文件被追踪 —— 必须移出"
    HITS=$((HITS+1))
  else
    echo "  无（04-restricted-materials 未被追踪）"
  fi
else
  echo "  (尚未初始化仓库，跳过)"
fi

echo "------------------------------------"
if [ "$HITS" -gt 0 ]; then
  echo " 扫描未通过: 存在 $HITS 类问题"
  exit 1
fi
echo " 扫描通过: 未发现凭证泄漏"
exit 0