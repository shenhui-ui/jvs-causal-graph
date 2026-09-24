# -*- coding: utf-8 -*-
"""生成冻结产物指纹基线 scripts/frozen-fingerprints.json。
这些产物的 SHA256 在 git 化前后必须完全一致（字节冻结策略的验收依据）。
"""
import os, json, hashlib

JVS = r"C:\Users\<user>\Desktop\JVS"
OUT = os.path.join(JVS, "scripts", "frozen-fingerprints.json")

# (用途说明, 相对路径)
ITEMS = [
    ("因果工具链-主流水线",      r"01-host-product\2026-08-28-20-59-40\tools\causal\s4_pipeline.py"),
    ("因果工具链-评判器",        r"01-host-product\2026-08-28-20-59-40\tools\causal\llm_judge.py"),
    ("因果工具链-中继调用",      r"01-host-product\2026-08-28-20-59-40\tools\causal\relay_call.py"),
    ("因果工具链-定向轮次",      r"01-host-product\2026-08-28-20-59-40\tools\causal\targeted_round.py"),
    ("隐私闸门-真名扫描",        r"01-host-product\2026-08-28-20-59-40\tools\host\name_scan.py"),
    ("冻结语料-393事件",         r"01-host-product\2026-08-28-20-59-40\docs\host_memory_dump\all-real-events.jsonl"),
    ("冻结图谱-549边",           r"01-host-product\2026-08-28-20-59-40\docs\host_memory_dump\all-real-edges-final.jsonl"),
    ("冻结图库-v3",              r"01-host-product\2026-08-28-20-59-40\docs\host_memory_dump\causal_graph_final_v3.db"),
    ("冻结题集-30题",            r"01-host-product\2026-08-28-20-59-40\docs\host_memory_dump\query-set-real-v1.jsonl"),
    ("验收台账-233单元",         r"03-d10-workspace\2026-09-08-20-30-19\outputs\full-review-full-20260909\acceptance-ledger.json"),
    ("交付包-事件复核",          r"03-d10-workspace\2026-09-08-20-30-19\outputs\full-review-full-20260909\final-review-package\event-reviews.json"),
    ("交付包-裁决日志",          r"03-d10-workspace\2026-09-08-20-30-19\outputs\full-review-full-20260909\final-review-package\adjudication-log.json"),
    ("交付包-证据索引",          r"03-d10-workspace\2026-09-08-20-30-19\outputs\full-review-full-20260909\final-review-package\evidence-index.json"),
]

os.makedirs(os.path.dirname(OUT), exist_ok=True)
rec = {
    "schema": "jvs-frozen-fingerprints/1",
    "created": "2026-09-16",
    "note": "字节冻结策略的验收基线。git 化与任何签出之后，这些 SHA256 必须保持不变。",
    "policy": "* -text（不做 EOL 转换），详见 GIT-POLICY.md 第三节",
    "items": [],
}
missing = []
for label, rel in ITEMS:
    p = os.path.join(JVS, rel)
    if not os.path.exists(p):
        missing.append(rel)
        continue
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    rec["items"].append({
        "label": label,
        "path": rel.replace("\\", "/"),
        "sha256": h,
        "bytes": os.path.getsize(p),
    })

json.dump(rec, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"已写出 {OUT}")
print(f"  收录 {len(rec['items'])} 项")
for it in rec["items"]:
    print(f"    {it['sha256'][:16]}  {it['bytes']:>9} B  {it['label']}")
if missing:
    print(f"  缺失 {len(missing)} 项（未收录）：")
    for m in missing:
        print(f"    {m}")