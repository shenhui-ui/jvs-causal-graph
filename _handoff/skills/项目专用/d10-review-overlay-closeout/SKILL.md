---
name: d10-review-overlay-closeout
description: D10 全量语义复核（工作区 C:\Users\<user>\host-workspace\2026-09-08-20-30-19）overlay 修订层（rN）的收口流程。当需要为某轮修订（如 r18/r19）落盘后做「独立重验 → 机械核验 → 台账/交付包重建 → 文档同步」，或排查「台账与磁盘 review 版本失同步」「层级回退」「入口文件过期」时使用。
agent_created: true
---

# D10 复核 overlay 层收口

## 何时用
- 某一轮 `review-revision-rN/` 已落盘（含回执），需要收口成可交付状态。
- 发现台账 `acceptance-ledger.json` 的 `review_sha256` / `current_review_revision_dir` 与磁盘现行版本不一致（「入口文件过期」类问题）。
- 重建后台账层级疑似回退。

## 关键路径
- 工作区 `W = C:\Users\<user>\host-workspace\2026-09-08-20-30-19`
- 交付根 `ROOT = W\outputs\full-review-full-20260909`
- payload 源 `C:\Users\<user>\host-workspace\outputs\m1-10-d10-event-extract-full-20260908\delivery-pro-r4\payloads\<chunk>.payload.txt`
- 冻结事件索引 `W\outputs\full-review-full-20260909-v2\batches\batch-NNN.json`
- 权威入口文档 `C:\Users\<user>\host-workspace\outputs\m1-10-d10-event-extract-full-20260908\RUNME-full.md` 与 `HANDOVER-20260915-R15.md`（交接件随轮次更新，**注意核对其文件名与版本号**）
- python `C:\Users\<user>\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe`
- 项目记忆 `W\.workbuddy-ai\memory\YYYY-MM-DD.md`（逐日追加）

## 四步收口（严格按序，不可跳步）

### 1. 独立重验（每个受影响的验收单元一份继任报告）
- 输出到 `ROOT\review-revision-rN\<unit>\independent-verification-rN.json`。
- **硬性格式（`_r16_check.py` 会机械判定）**：
  - `semantic_review_performed: true`、`read_only: true`
  - `overall_conclusion` 必须以 `"accepted"` 开头
  - `scope.batches` 与**旧报告逐字一致**（顺序与字面都不许变）
    - **例外（合并式取代）**：同一单元若存在多个历史**分片**报告（如某单元 r3 绑 090/092、r13 绑 091），
      新报告可用**并集 tuple**（090+091+092）。此时旧报告 tuple ≠ 新 tuple，其在台账中走
      「**不再绑定当前版本**」路径退出 `effective_open`（而非「被取代」）——两条路径实质等价，
      `superseded` 计数不增、`binding_current` 变化。核验脚本对旧报告只要求「旧 tuple ⊆ 新 tuple」+「对当前版失绑」。
  - `artifacts_sha256` 键用 basename（`batch-NNN.review.json`），值 = 该批**磁盘现行版本** SHA
  - **基线/对照副本一律放 `comparison_inputs_sha256`**；严禁把基线以 `batch-NNN.review.json` 这类 basename 放进 `artifacts_sha256`（撞名会判 ambiguous、验收无法升级）
  - 顶层键与同单元旧报告同构；`findings` 每条含 `id/severity/blocking`（布尔显式）
- 代理必须**自己直算**（递归 diff、引文逐字节复算、tags 以冻结事件为基的覆盖复算），不得采信落盘方自报。
- 代理**只能写自己那一份报告**；不得改 payload/review/校验件/回执，也不得写包内目录。

### 2. 机械核验
```
"$PY" _r16_check.py          # 预期 13/13 OK、0 fail（SPECS 里已含各轮报告条目）
```
含取代前提校验：旧报告对当前版**必须全部失绑**、scope tuple 逐字一致、chain 报告亦失绑。

### 3. 重建台账 + 交付包（零回退）
```
"$PY" _r17_rebuild.py        # 轮次不同时改用对应 _rNN_rebuild.py
```
- **重建前必须先确认台账快照 SHA == 预期基线**，否则「零回退」比对退化为自身比对、不具证明力（曾真实踩坑）。
- 断言要点：触达集恰为该轮变更批次；同单元未改批（如 c05）level/SHA 必须不变；无降级、无 `structural_only`；两次重建包 SHA 一致；testzip 干净；包内台账与根台账逐字节一致。
- **基线的正确做法（推荐）**：用主构建器**自带**的 `--round` 参数，在**当前磁盘**复现上一轮收口态，作为权威基线文件。**一条命令，不必复制 `.bak`、不必改输出路径、不要求 CWD 在工作区根目录**：
  ```bash
  "$PY" build_acceptance_ledger.py --round r17 --out _ledger_r17_baseline.json
  ```
  `--round rN` 只做**一件事**：把 `REV_DIRS` 截断到 rN（含）。复现值应与原基线**逐项一致**（r17 → 层级 89/139/5、eff_open 102）。
  - `--out` 为相对路径时按 `outputs/full-review-full-20260909/` 解析，绝对路径原样使用；`--round staging` = 只含 `review-staging`；省略 `--round` = 全量。
  - ⚠️ `--round` **只覆盖「`REV_DIRS` 尾部截断」这 1 条差异轴**。另 4 条历史差异轴（受阻批次授权集、受阻 `reason` 措辞、输出文件名、行尾 CRLF/LF）**不参数化** ⇒ 它只能用于**复现基线**，不能用来重放历史轮次的其他差异。
  - ⚠️ `.bak` 文件名**不可信**：`…bak-20260915-r13` 实际只到 r12，`-r17`/`-r18`/`-r19` 亦各少一轮。判断某快照的轮次要看它自己的 `REV_DIRS` **末元素**，不要看文件名。
  - 2026-09-20 起 25 个 `.bak-*` 已迁 `_archive/bak-2026-09/`（保留原始相对路径），3 个 `_build_ledger_*_baseline.py` 已迁 `_archive/superseded-20260920/`。
  - 复现基线的 findings **总数**可能因新层报告已在盘而偏高（属预期，不视为失败）；判定一致性看**层级 + `open_findings_effective_open`**。
- **`open_findings_count` 会「虚增」**：新层报告已落盘、但该层未进 `REV_DIRS` 时执行过重建，会把新报告 findings 计入总数却与其取代关系脱钩。**实质口径一律只看 `open_findings_effective_open`**，并在文档中显式说明该虚增非新增缺陷。

### 4. 文档同步（否则「入口文件过期」复发）
同步 `RUNME-full.md` 与最新交接件：层级批/事件数、证据件数、两处 SHA、findings 口径、新轮小节、`REV_DIRS` 提示、完成态句。
用脚本化替换 + 残留旧值 grep 复核（`1e4d5c8f`/`740,212`/`507件` 之类旧值不得残留，历史/transition 表述除外）。

## 踩坑清单（血泪）
1. **`REV_DIRS` vs 全域扫描**：`REV_DIRS`（`build_acceptance_ledger.py`）决定「当前版本」；台账另用 `os.walk(ROOT)` 收集 `independent-verification*.json` 决定「取代来源」。二者职责不同——**新层报告已落盘但层未进 `REV_DIRS` 时重建，会得到层级不变的混合态**，不可作交付。
2. **单元捆绑迁移**：某批改层时，其验收报告 `scope.batches` tuple 内**全部批次**必须同层迁移（未改批也要带迁移标记使 SHA 变化），否则未迁移批的当前目录内已无「绑定当前版本」的验收件，层级会回退。
3. **校验 stage 新鲜度**：组装 `_validation-stage-rN/<chunk>/` 时每批取**现行版本**（按层优先级从磁盘解析），不要用 shell 通配 `head -1`（字母序会把 r3 排前），否则校验件对兄弟批只背书到旧层。
4. **链式编辑**：同一批多轮落盘时 `sha_prev` 取**上一版写后值**；落盘脚本**一律不得对既有 overlay 层目录 rmtree**（曾误删 r7/G13-c01）。
5. **并行编辑同一文件**：同一文件的多处 `Edit` 必须**串行**，并行会互相覆盖（本轮真实发生：一次编辑被另一次写回覆盖）。
6. **抽样外推 ≠ 全量数**：判据类缺陷必须严格模式全库枚举后再落盘。
7. **先回源、后落盘、与重验成对执行**；裁定优先于改动（交叉一致性问题先查上游冻结集差异，不得伪造冻结事件）。
8. **`records` 只是范围下限，不是全量**：findings 的 `records` 与回执声明的改动集**都只是下限** —— 执行切片时必须以**全量机械枚举**收口（r18 正是靠枚举，在重验者登记 3 条之外另捕获同族第 4 条）。反之，凡**超出** `records` 的同族项**不判 blocking**（不属「声明不符」），且按「**已验收层不追加落盘**」纪律记入下一轮切片，不得擅自补落。
9. **写前 diff 白名单断言**：落盘脚本在**写盘之前**就要断言「递归 diff 路径**必须全部落在**允许改动的字段集内」（如仅 `$.decisions[i].reason` 与 `$.review_method`）；这条断言能一次性杜绝越界，比事后复核更可靠。同时先探测源文件能否**字节级往返**（`json.dumps(ensure_ascii=False,indent=2)` 换 CRLF 后比对原文），不能往返就说明序列化约定不符，必须停下查清。
10. **纯净性优先选切片**：从剩余 findings 中选下一轮切片时，优先取「**零值位改动、纯文本/记账类**」的簇（如 reason 措辞对齐、note 措辞归一）——风险最低、记录数最大、可一批清收；**值位改动类**必须逐条回源、成本高，宜单独成轮。
11. **切片前提必须双向回源**（r19 血泪）：**不要直接沿用上一轮交接件写好的切片建议**，必须先回源验证其前提。尤其「措辞类残留」要**双向**比对：诊断句 vs **冻结输入**（如 `outputs/full-review-full-20260909-v2/batches/batch-NNN.json` 的 `event`），且 vs **落盘值**。r19 的切片①（同族 reason 措辞残留 4 条）即因诊断句引述的是**修订前（=冻结）值**、落盘值已为修正式而被**证伪、当即作废**——reason 本身准确。做法：对候选切片先做一次「全库机械枚举 + 逐条字节级对照冻结/落盘两侧」的可行性探测，通过才落盘。
12. **`effective_open` 净额持平 ≠ 无进展**（r19）：重验者可各自登记**过渡性 `FR` 项**（如追溯性说明、上游文案勘误），使 eff_open 净额持平。必须按「**退出 / 承接 / 新登记**」三段归因解读，并**独立复算每条新登记项是否真实**（r19 的 c06 FR1 经直接复算确认系上游报告叙述统计勘误，属真 finding）；净 0 时不要把结论写成「无变化」。
13. **复用既有先例，不自创措辞**（r19）：批量归一某字段措辞前，先**全库枚举**是否已有更准确的落地措辞（r19 发现全库已有 175 条 SC 使用「新增候选」措辞，直接采用而非自创），符合「成熟方案复用」原则并降低风格漂移。
14. **文档行尾陷阱**（r19 真实踩坑）：`HANDOVER-*.md` 是 **CRLF**、`RUNME-full.md` 是 **LF**；用 heredoc 脚本插入新段落时容易引入**混合行尾**。可靠做法：用 `s.split("\r\n")`（CRLF 文档）或 `s.split("\n")`（LF 文档）**按行号插入**，写回时用对应分隔符 join；插入后跑一次「CRLF 归一化 + `assert 忽略行尾后内容全等`」的校验。

## 完成后

通用收尾序列见 `_handoff/DEFINITION-OF-DONE.md`（相对 JVS 仓库根）。
本技能**无专属收尾项** ⇒ 走 §1 §4 §5 §6 §9；交付物（台账 / 包 / RUNME / 交接件）用 `present_files` 展示。
