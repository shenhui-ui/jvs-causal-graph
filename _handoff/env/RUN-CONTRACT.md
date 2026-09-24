# 运行目录契约 —— 关键，接手前必读

> 2026-09-16 实测 ｜ 本文件说明**脚本必须在哪个目录下运行**

---

## 一、最重要的一条

### `03-d10-workspace/2026-09-08-20-30-19/` 的顶层脚本

**必须在该目录内运行，不能在别处运行。**（顶层 `.py` 数量会变，现测：`ls <该目录>/*.py | wc -l`；2026-09-20 为 266。）

原因（实测）：

| 事实 | 数量 | 后果 |
|---|---|---|
| 脚本引用了 `outputs/full-review` | **178 个** | 这些是 **CWD 相对路径**，换个目录就找不到 |
| 脚本内写死老绝对路径 `C:\Users\<user>\host-workspace\...` | 多处 | 靠 **Junction 透明解析**才能生效 |

正确姿势：

```bash
cd "C:/Users/<user>/jvs-src/03-d10-workspace/2026-09-08-20-30-19"
"C:/Users/<user>/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe" -B _r19_validate.py
```

 错误姿势：

```bash
# 从 JVS 根目录跑 —— 相对路径 outputs/ 会解析到 JVS/outputs（不存在）
cd "C:/Users/<user>/jvs-src"
python 03-d10-workspace/.../_r19_validate.py
```

**为什么不能改成绝对路径**：脚本互相 `import`（同目录模块），且 178 处相对路径改动面太大、
易引入回归。当前形态是**经过 19 轮修订验证的既定工作方式**，不要动。

---

## 二、Junction 与老路径的关系

脚本里的老绝对路径 `C:\Users\<user>\host-workspace\...` **仍然有效**，因为：

```
C:\Users\<user>\host-workspace\2026-09-08-20-30-19   →  Junction  →  JVS\03-d10-workspace\2026-09-08-20-30-19
C:\Users\<user>\host-workspace\outputs               →  Junction  →  JVS\02-m1-evaluation\outputs
C:\Users\<user>\host-workspace\2026-08-28-20-59-40   →  Junction  →  JVS\01-host-product\2026-08-28-20-59-40
C:\Users\<user>\host-workspace\restricted-review     →  Junction  →  JVS\04-restricted-materials\restricted-review
```

**共 24 个 Junction**（顶层 3 + `outputs` 下 22 个子项）。

### 接手时请先验证 Junction 是否还在

```bash
bash _handoff/env/check_env.sh     # 第 [7] 项即为 Junction 完整性检查
```

如果 Junction 丢失（例如手工删过目录），脚本里的老绝对路径会全部失效。
重建方法见 `MIGRATION-RECORD.md` 第 6 节「回滚方式」的反向操作。

---

## 三、各分区运行基准目录

| 分区 | 运行基准目录 | 说明 |
|---|---|---|
| **01 工具链** | `01-host-product/2026-08-28-20-59-40/tools/causal/` | `s4_pipeline.py` 等同目录 `import` |
| **01 评测** | `01-host-product/2026-08-28-20-59-40/tools/eval/` | `eval_m1.py` 独立可跑 |
| **01 宿主采集** | `01-host-product/2026-08-28-20-59-40/tools/host/` | `name_scan.py` 独立可跑 |
| **02 评测切片** | 各切片自身目录 | 各自独立 |
| **03 复核机械** | `03-d10-workspace/2026-09-08-20-30-19/` | **必须是这个目录** |
| **04 受限** | `04-restricted-materials/restricted-review/` | D09 脚本以此为受控读取根 |
| **索引/工程** | `JVS/` 根 | `_migrate/*` 用绝对路径，任意目录可跑 |

---

## 四、数据资产的实际位置

脚本常按**旧路径**找数据，实际文件在：

| 逻辑位置 | 实际物理位置 | 访问方式 |
|---|---|---|
| `.../docs/host_memory_dump/all-real-events.jsonl` | `01-host-product/2026-08-28-20-59-40/docs/host_memory_dump/` | 相对 `01` 或经 Junction |
| `.../outputs/full-review-full-20260909/` | `03-d10-workspace/2026-09-08-20-30-19/outputs/full-review-full-20260909/` | **相对 03 目录** |
| `outputs/m1-10-d10-holdout-20260916/` | `02-m1-evaluation/outputs/m1-10-d10-holdout-20260916/` | 绝对或经 Junction |

---

## 五、冻结产物清单（**禁止覆盖**）

| 产物 | 路径 | 状态 |
|---|---|---|
| 冻结事件库 | `docs/host_memory_dump/all-real-events.jsonl` | **393 事件，ID 冻结** |
| 冻结边库 | `docs/host_memory_dump/all-real-edges-final.jsonl` | 549 边 |
| 冻结图谱 | `docs/host_memory_dump/causal_graph_final_v3.db` | **v3 定版** |
| 题集 v1 | `docs/host_memory_dump/query-set-real-v1.jsonl` | **30 题，主验收分母** |
| gold | `docs/host_memory_dump/m1_10_baseline_final/gold.jsonl` | **固定分母** |
| 原始判分 | `docs/host_memory_dump/m1_10_baseline_final/` | **21/30 不覆盖** |
| 复核台账 | `03-.../outputs/full-review-full-20260909/acceptance-ledger.json` | 233 单元 / 3732 事件 |

**重跑请一律用新输出路径**，例如 `scores-sn-new.json`、`--out <新目录>/`。

---

## 六、`outputs` 目录的特殊性

`C:\Users\<user>\host-workspace\outputs` **本身是实体目录**（不是 Junction），
其下 **22 个子项各自是 Junction**。

原因：迁移时该目录被宿主进程持有句柄，`rename` 被拒（WinError 5），
故改为逐子项搬迁 + 子项级 Junction。**效果等价，无需任何代码改动。**

唯一未迁移的项：文件 `m1-10-source-decisions-20260907-pending.md`（留在原地）。