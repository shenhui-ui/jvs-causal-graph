# 迁移记录 —— `host-workspace\` → `Desktop\JVS\`

> 执行日期：2026-09-16 ｜ 执行方式：真实字节移动 + 原路径留 Junction
> 结果：**全部 PASS，无数据丢失**

---

## 1. 迁移前后对照

| # | 原路径 | 新物理路径 | 体量 | 文件数 |
|---|---|---|---|---|
| 1 | `host-workspace\2026-08-28-20-59-40` | `01-host-product\2026-08-28-20-59-40` | 17.7 MB | 826 |
| 2 | `host-workspace\outputs\*`（22 个子项） | `02-m1-evaluation\outputs\*` | 3,866.7 MB | 7,373 |
| 3 | `host-workspace\2026-09-08-20-30-19` | `03-d10-workspace\2026-09-08-20-30-19` | 200.4 MB | 13,063 |
| 4 | `host-workspace\restricted-review` | `04-restricted-materials\restricted-review` | 2.8 MB | 34 |
| | **合计** | | **≈ 3.81 GB** | **21,296** |

## 2. Junction 清单（24 个）

**顶层 3 个**

```
host-workspace\2026-08-28-20-59-40  ->  JVS\01-host-product\2026-08-28-20-59-40
host-workspace\2026-09-08-20-30-19  ->  JVS\03-d10-workspace\2026-09-08-20-30-19
host-workspace\restricted-review    ->  JVS\04-restricted-materials\restricted-review
```

**`host-workspace\outputs\` 下 22 个子项**（各自 Junction 到 `02-m1-evaluation\outputs\`）

> `outputs` 目录**本身保留为实体目录**（未迁移），因为宿主进程持有其句柄、`rename` 被拒
> （WinError 5）。改为逐子项搬迁 + 子项级 Junction，效果等价且不影响任何路径解析。
> 唯一未迁的子项是文件 `m1-10-source-decisions-20260907-pending.md`，留在原地。

## 3. 校验结果（S5）

| 检查项 | 结果 |
|---|---|
| 顶层 junction 可达（reparse + isdir） | **PASS** ×3 |
| `outputs` 子项 junction 覆盖 | **PASS** 22/22 |
| 关键文件 SHA 比对（9 个，经原路径读取） | **PASS** 9/9，与迁移前完全一致 |
| 物理位置交叉验证（JVS 下直接读） | **PASS** ×4 |
| 文件总数（21,296 → 21,300，+4 为 `_migrate` 新增脚本） | **PASS** |
| `outputs` 原路径读写删 | **PASS** |

关键 SHA（迁移前后一致）：

```
39f7bae9feb46d02  2026-09-08-20-30-19\outputs\full-review-full-20260909\acceptance-ledger.json
648546f645c027a8  2026-09-08-20-30-19\review_scoped_validator.py
d80372e0c7399e9e  2026-09-08-20-30-19\review_full_validator.py
69a439ced2f23755  2026-09-08-20-30-19\build_acceptance_ledger.py
0b526bc31327b6a2  outputs\m1-10-d10-holdout-20260916\questions.json
6f72e4da8e70ee6e  outputs\m1-10-d10-holdout-20260916\overview.md
2eeafbcd6e98c794  outputs\m1-10-d10-independence-check-20260916\report.md
e510aa78aede6e0c  2026-08-28-20-59-40\docs\M1-立项书-v0.6.md
7a5a026e453ae643  2026-08-28-20-59-40\docs\README-版本说明.md
```

> ⚠️ **本节是 2026-09-16 迁移时刻的冻结记录，不得回填。**
> 上表「迁移前后一致」只对该时刻成立；迁移**之后**被有意修改的文件会让本表与当前树不符
> —— 那是**预期行为，不是迁移失败**。
> **已知现状（现测 2026-09-21：9 条里 5 条仍逐字节一致、4 条已变）**：
> · `review_scoped_validator.py` `648546f6…` → `b623c12f…`（2026-09-21 C-2 拆分首个目标）
> · `review_full_validator.py` `d80372e0…` → `aa658ebe…`（2026-09-21 C-2 拆分第二个目标）
> · `build_acceptance_ledger.py` `69a439ce…` → `321879c9…`（2026-09-20 阶段 B item 1）
> · `outputs/m1-10-d10-holdout-20260916/questions.json` `0b526bc3…` → `ee5dd928…`
> 同一批冻结期望值亦见 `_migrate/s5_verify.py` 的 `S1_KEYS`（该脚本头部已加同样的冻结点说明）。
> 权威的**冻结面**红线是 `scripts/frozen-fingerprints.json` 的 13 项，**上表 9 项均不在其中**。
> 详见 `REFACTOR-PLAN.md` §三 R4 但书块 / `PHASE-C-PLAN.md` §3.3、§3.4。

## 4. 真实校验器实测

`review_scoped_validator` 单元测试：**31 项，29 PASS / 2 FAIL**。

**这 2 个 FAIL 与迁移无关**，已定位为迁移前既有的测试陈旧：

- 失败用例：`test_payload_cannot_point_to_forbidden_metadata_or_batch`（`batch-170`、`batch-232`）
- 根因：`outputs/full-review-full-20260909/blocked-authorization.json`（2026-09-15 项目方授权）
  把这 16 个批次从 BLOCKED 名单豁免，而测试用例仍按旧预期断言「BLOCKED 批次不得被选为 payload」
- 证据：`BLOCKED 含 170 = False`、`BLOCKED 含 232 = False`，默认集 `default` 仍含二者
- 结论：属 **测试预期过期**，需更新用例；**非迁移引入**

同时该实测**反证了 junction 语义完好**：`blocked_authorization.py` 用
`Path(__file__).resolve()` 解析，traceback 显示模块正确落到
`Desktop\JVS\03-d10-workspace\...` 物理路径且授权文件可正常读取 —— 说明
`realpath` 类调用在 junction 下工作正常。

## 5. 已知风险与实况

| 预案风险 | 实况 |
|---|---|
| `realpath` 解析到物理路径致相对路径语义变化 | **未发生**，校验器实测通过 |
| WorkBuddy 自身不认 junction 化工作区 | **未发生**，原路径访问正常 |
| 受限素材随项目移动扩大访问面 | 已隔离至 `04-restricted-materials`，README 已标注不得外发 |
| 迁移中断 | 未发生；同盘移动为 MFT 级操作 |

## 6. 回滚方式

如需还原到迁移前状态：

```cmd
:: 1) 删除 junction（只删联接，不删数据）
rmdir "C:\Users\<user>\host-workspace\2026-08-28-20-59-40"
rmdir "C:\Users\<user>\host-workspace\2026-09-08-20-30-19"
rmdir "C:\Users\<user>\host-workspace\restricted-review"
:: outputs 下 22 个子项同理逐个 rmdir

:: 2) 把真实字节移回原位
move "C:\Users\<user>\Desktop\JVS\01-host-product\2026-08-28-20-59-40" "C:\Users\<user>\host-workspace\2026-08-28-20-59-40"
move "C:\Users\<user>\Desktop\JVS\03-d10-workspace\2026-09-08-20-30-19" "C:\Users\<user>\host-workspace\2026-09-08-20-30-19"
move "C:\Users\<user>\Desktop\JVS\04-restricted-materials\restricted-review" "C:\Users\<user>\host-workspace\restricted-review"
```

> ⚠️ 必须用 `rmdir`（删除目录联接），**不可用 `rmdir /s /q` 或 `rm -rf`**——那会删掉真实数据。

## 7. 执行脚本与产物

| 文件 | 用途 |
|---|---|
| `JVS\_migrate\s2s4_migrate2.py` | S2–S4 迁移主脚本（含续跑逻辑） |
| `JVS\_migrate\move_outputs_children.py` | `outputs` 逐子项搬迁专用 |
| `JVS\_migrate\s5_verify.py` | S5 迁移后校验 |
| `02-m1-evaluation\outputs\migration-plan-20260916\s1_baseline.py` | S1 只读基线 |
| `...\s1-manifest.json` / `s1-full-manifest.jsonl` | 迁移前清单与逐文件 SHA 基线 |
| `...\s2s4-result.json` / `s2_children-result.json` | 迁移执行结果台账 |
| `...\PLAN.md` | 迁移方案（勘察阶段） |

## 8. 本次未做的事

- 未删除任何文件
- 未批量改写任何脚本里的路径（全程依赖 junction）
- 未触碰其余 21 个 `2026-*` 历史工作区
- 未使用 `rm -rf` 或任何递归删除