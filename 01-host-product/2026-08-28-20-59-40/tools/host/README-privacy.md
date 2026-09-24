# 隐私管线(脱敏 v2 + 真名扫描闸门)

> 更新:2026-09-24 ｜ 关联:`docs\M1-风险登记-20260830-脱敏缺口.md`(B 级)｜ 原则:任何上云 payload 必须先过脱敏与闸门
>
> ⚠️ **本文件属公开层**：真实姓名 / 代号映射表 / 身份表均**不在本仓库**（私有层）。

## 流水线(新语料/新批次的固定流程)

```
原始备份 JSON ──> desensitize_feishu.py(v2) ──> 脱敏时间线(留档 *-v2.md)
                                     │
                                     └─> payload 组装(compose_*)
                                           │
                                           └─> name_scan.py --check <payload> --names <词表.json>   ←【闸门:命中即拒发,exit=1】
                                                 │
                                                 └─> 放行 → 才允许调用 LLM
```

## 1. 脱敏器 v2:`tools\causal\desensitize_feishu.py`

覆盖(相对 v1 的修复):
- ① 顶层发送者 → 代号;② `@提及` → 代号;③ **正文(含嵌套引用记录)全名/昵称 → 代号**;
- ④ **`--extra-names`**:覆盖「仅出现在正文、非发送者」的人 —— 不带此参数会漏(实测:漏 5 人 20+ 处);
- ⑤ **别名表**(昵称 → 全名 → 代号);
- ⑥ 链接/UUID/token 脱敏保留。

**标准调用**(以身份表全量名单为 extra-names):
```powershell
$names = Get-Content identities-init.json -Raw | ConvertFrom-Json
$nstr  = ($names.identities | % canonical_name | ? length -ge 2 | sort -u) -join ','
python desensitize_feishu.py --input <backup.json> --out <时间线-v2.md> --mapping <映射-v2.md> --known <==> --extra-names $nstr
```

## 2. 闸门:`tools\host\name_scan.py`

⭐ **词表必须外部提供 —— 本文件不含任何真实姓名。**

- `--check <payload|dump.jsonl|目录>`;`--names <词表.json>` 指定词表,`--names-dir <目录>` 合并目录下全部 `*.json`;
- **exit 0=放行 / exit 1=命中拦截 / exit 2=输入或词表错误**;集成点:compose_* 生成 payload 后、调用 `openclaw agent` 前;
- ⭐ **未提供词表 / 词表为空 / 有效词条为 0 ⇒ 主动报错并返回 2（拒绝运行）** ——
  因为「空词表 ⇒ 扫描通过」会输出**虚假的干净结论**,这比报错危险得多;
- 词表结构见同目录 **`names-example.json`**（**虚构**示例,演示两种格式:
  `{"identities":[{canonical_name, aliases, note}]}` 或顶层 `{"姓名": ["别名"]}`）;
- 占位符条目（如「待补全-01」）与非自然人条目由 `_is_non_person` 按**结构化字段**自动排除出词表
  （只认结构化证据,不按名称子串猜测 —— 漏报真名是安全风险,故排除须有据）;
- 扫描目录会递归检查支持的文本文件；目标不存在、无可扫描文件或任一文件读取失败均拒绝放行(exit=2)，命中敏感词仍为 exit=1。
- 敏感易漏提示:昵称/嵌套引用/图片文件名。

## 3. 历史数据处理(处置结论)

- 历史批次已上云(姓名级低敏,同事姓名)——**不重刷**(会动 393 冻结);
- `*-v2.md` 已重新生成留档,正文区**零残留**;
- **M2 数据清洗**:用 v2 产物重建语料;如需完全消除历史暴露 → 重跑历史批(≈1 人日 + 评审会批准,见风险登记)。