---
name: jvs-governance-version-landing
description: 把一项治理裁定落进 JVS 证据治理包的**新版本**（vN）—— 只新建版本目录、绝不改写旧版，父版本字节哈希绑定，窄口径只落被批准的那一项，其余显式未批准。当出现「把裁定落进新版本」「落版 v4/v5」「evidence-governance-20260907-vN」「D07/D08 归一化映射」「canonical_corpus 保持 null」「父版本被改写」时使用。含三段式 build/verify/validate、隐私闸门预热坑、单次运行内提交与读 HEAD 复核。
agent_created: true
---

# JVS 证据治理包「版本落版」

## 何时用

- 要把某条治理裁定（`D01`–`D10` 之一）的结论**落进新版本**；
- 要新增 `02-m1-evaluation/outputs/m1-10-evidence-governance-20260907-vN/`；
- 出现「旧版被改写」「`parent_hashes` 不匹配」「`canonical_corpus` 被填了值」等异常。

**不适用**：改因果边库（用 `jvs-causal-edge-arbitration`）、刷新功能索引（用 `jvs-index-layer-maintenance`）。

## 铁律（违反即结论不可信）

1. **旧版一律不可改写。** 改决定只能**另建版本**。依据：v1 `overview.md` 第 59 行
   「批准事项须带具体选择、独立依据、审阅者、确认时间和目标版本；**应另建后续版本保存决定，
   不能直接改写已绑定的 v1 文件**」；来源链审计亦声明「原始 v1 登记和 v2/v3 候选**不作事后改写**」。
2. **不捆绑批准。** 裁定说「只接纳 X，不捆绑 Y/Z」时，Y/Z 必须在新版里显式写 `false`，
   而不是「没提」。已踩案例：裁定 #6 只接纳「本地归一化输入映射」，
   ⇒ `canonical_corpus` 必须**保持 `null`**，另三项 `*_verified` 全 `false`。
3. **候选 ≠ 生产。** 全包 `production_applied` / `production_integrated` 为 `false`；
   只提供 `build` / `verify` / `validate` 本地命令，**无 apply、无网络入口、无生产挂接**。
4. **旧断言留痕不抹除。** 被取代的状态写进 `previous_status_v1` 一类字段，不要直接覆盖。
5. **不复制原文。** 只放哈希、相对定位、编号；`PrivacyGate` 会拒 URL / 邮箱 / 密钥 / 手机号。

## 动手前必须核清的 4 件事

1. **裁定原文**（含「未采纳」项与「必须同时保留的限制」）—— 出处通常是
   `01-host-product/.../docs/host_memory_dump/M1-10-待裁定项决策记录-YYYYMMDD.md`。
2. **旧版当前值** —— 直接读 `v1/source-registry.json` / `v3/decision-state.json`，
   **不要凭记忆**；新版要把旧值放进 `previous_status_v1` 并在校验里比对。
3. **证据包的字节哈希** —— 审计/调查目录（如 `m1-10-source-chain-audit-20260907/`）
   四个文件全绑进 `evidence_binding`。
4. **「新版本」的口径** —— 窄口径（只落被批准那一项）还是完整候选。
   ⚠️ 这决定产物形态，**先问用户**，别自行扩大。

## 版本包的标准构成（9 文件）

| 文件 | 作用 |
|---|---|
| `plan-before-vN.md` | **动手前**写下的计划快照（生成产物之前） |
| `source-registry.json` | `schema m1-10-source-registry/2`；被批准项的核验结论 + 未捆绑项 |
| `decision-state.json` | `schema m1-10-decision-state/4`；D01–D10 状态；继承项标 `inherited_from` |
| `user-confirmation.json` | 绑定用户选择原文 + `authorization_scope` + 各项 `*_authorized: false` |
| `overview.md` | 本轮说明 / 未捆绑范围 / **必须保留的限制** / 验证范围 / 保留状态 |
| `manifest.json` | 父版本字节哈希 `parent_hashes` + 本包 `file_hashes`（不含 manifest 自身） |
| `verification.json` | 测试计数、`manifest_sha256`、`code_sha256`、隐私计数、`remaining_gates` |
| `source_mapping_vN.py` | `build` / `verify` / `validate`（`build` 拒绝覆盖已存在文件） |
| `test_source_mapping_vN.py` | 聚焦边界测试 |

`manifest.file_hashes` 覆盖数据与文档（3 JSON + 2 MD）；**代码只绑在 `verification.json.code_sha256`** ——
否则每次改脚本都要重建 manifest，迭代成本过高。

## 三段式命令（避开循环依赖）

`verification.json` 要在**测试计数之后**写，而测试又要读它 ⇒ **不要用两段式**。用：

```
python -B source_mapping_vN.py build     # 写数据 + manifest（拒绝覆盖）
python -B source_mapping_vN.py verify    # 跑测试 → 写 verification.json → 再跑一遍确认
python -B source_mapping_vN.py validate  # 只读复验（strict：要求 verification.json 存在）
```

**硬约束**：`verify()` 的第一轮测试**必须能通过**（那时 `verification.json` 还不存在）
⇒ **任何测试都不得要求 `verification.json` 必须存在**（`setUpClass` 里用 `if exists` 兜）。

## 坑：隐私闸门必须在进入 `offline()` 之前预热

`offline()` 用「把 `socket.socket` 换成普通函数」来断网。但 `PrivacyGate` 所在模块会
`import urllib.request` → `http.client` → `ssl`，而 `ssl` 在导入时要执行 `class SSLSocket(socket)` ——
`socket.socket` 已不是类 ⇒ `TypeError: function() argument 'code' must be code, not str`。

**修法**：闸门做**模块级缓存**，并在 `verify()` / `validate()` 里**先** `privacy_gate()`、**再** `with offline():`。

## 落库纪律（单次运行内完成）

本环境会回滚仓内编辑，且**粒度不是整文件** ⇒ 把「改文件 + `git add` + `git commit` + 复核」
压在**一条 bash 命令**里，用 `&&` 串起来。通用序列见 `_handoff/DEFINITION-OF-DONE.md`。

**本技能的两处专属差异**：

- `git add` 一律用**显式路径**，不用 `git add -A`（避免误纳环境备份文件）；
- 提交信息写进**工作区外**的文件，用 `git commit -F <文件>`（长信息不用 heredoc）。

⚠️ 绝不使用 `git switch` / `git checkout`（本环境必发 SIGTERM，会把工作区文件批量移入回收站）；
单条 git 命令处理 ≤3,000 文件。

⚠️ 绝不使用 `git switch` / `git checkout`（本环境必发 SIGTERM，会把工作区文件批量移入回收站）；
单条 git 命令处理 ≤3,000 文件。

## 顺带更正静默过期

落版时**必须扫一遍** `_migrate/status_map.py`、`_handoff/HANDOFF.md`、`_handoff/PENDING.md`：
与新裁定**相反**的旧断言（尤其「用户选择继续待审」这类）会静默存活。
已踩：2026-09-20 一次更正 4 处（来源链审计 / D10 交付物 / 零修订审计 / M1 验收）。

## 交付前校验清单（本技能专属项）

> 通用收尾序列见 `_handoff/DEFINITION-OF-DONE.md`（相对 JVS 仓库根）。

- [ ] `validate` 输出 `status: passed`、`canonical_corpus_populated: false`、`prior_versions_unchanged: true`
- [ ] 未捆绑项在 `registry` / `decision-state` / `user-confirmation` **三处一致**
- [ ] `manifest.parent_hashes` == 磁盘上 v1/v2/v3 的实际哈希
- [ ] 隐私计数**全 0**（含 `url`）
- [ ] `git ls-tree -r --name-only HEAD -- <版本目录>` 文件数 == 9
