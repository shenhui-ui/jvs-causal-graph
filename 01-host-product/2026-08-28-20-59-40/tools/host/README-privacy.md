# 隐私管线(脱敏 v2 + 真名扫描闸门)

> 更新:2026-08-30 ｜ 关联:`docs\M1-风险登记-20260830-脱敏缺口.md`(B 级)｜ 原则:任何上云 payload 必须先过脱敏与闸门

## 流水线(新语料/新批次的固定流程)

```
原始备份 JSON ──> desensitize_feishu.py(v2) ──> 脱敏时间线(留档 *-v2.md)
                                     │
                                     └─> payload 组装(compose_*) 
                                           │
                                           └─> name_scan.py --check <payload>   ←【闸门:命中即拒发,exit=1】
                                                 │
                                                 └─> 放行 → 才允许调用 LLM
```

## 1. 脱敏器 v2:`tools\causal\desensitize_feishu.py`

覆盖(相对 v1 的修复):
- ① 顶层发送者 → Z 码;② `@提及` → Z 码;③ **正文(含嵌套引用记录)全名/昵称 → Z 码**;
- ④ **`--extra-names`**:覆盖「仅出现在正文、非发送者」的人(如Z39/Z01/Z-UNMAPPED)——不带此参数会漏(实测:漏 5 人 20+ 处);
- ⑤ 别名表(Z-UNMAPPED/Z18/Z01/Z-ALIAS → 对应真名 → Z 码);
- ⑥ 链接/UUID/token 脱敏保留。

**标准调用**(以身份表全量名单为 extra-names):
```powershell
$names = Get-Content identities-init.json -Raw | ConvertFrom-Json
$nstr  = ($names.identities | % canonical_name | ? length -ge 2 | sort -u) -join ','
python desensitize_feishu.py --input <backup.json> --out <时间线-v2.md> --mapping <映射-v2.md> --known <==> --extra-names $nstr
```

## 2. 闸门:`tools\host\name_scan.py`

- `--check <payload|dump.jsonl|目录>`;内置 66 人名单 + 别名;`--names identities-init.json` 可换表;
- **exit 0=放行 / exit 1=命中拦截 / exit 2=输入或读取失败**;集成点:compose_* 生成 payload 后、调用 `openclaw agent` 前;
- `--out` 主产物仅包含代号化正文，不含反向映射；映射只能写入显式 `--mapping` 指定的受控文件，禁止将映射文件作为 payload。
- 扫描目录会递归检查支持的文本文件；目标不存在、无可扫描文件或任一文件读取失败均拒绝放行(exit=2)，命中敏感词仍为 exit=1。
- 敏感易漏提示:昵称/嵌套引用/图片文件名。

## 3. 历史数据处理(处置结论)

- 历史 14 批已上云(姓名级低敏,同事姓名)——**不重刷**(会动 393 冻结);
- `*-v2.md` 已重新生成留档(语料组B 66 人表/语料组A 67 人表),正文区**零残留**;
- **M2 数据清洗**:用 v2 产物重建语料;如需完全消除历史暴露 → 重跑 14 批(≈1 人日 + 评审会批准,见风险登记)。
