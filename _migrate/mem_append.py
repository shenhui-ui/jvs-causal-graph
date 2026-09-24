# -*- coding: utf-8 -*-
r"""工作区记忆块「幂等追加」统一入口。

合并自 `append_ws_memory.py` / `append_ws_memory2.py` / `append_ws_memory3.py`
（2026-09-16 会话的三段记忆块；三者的**骨架逐字节同构**，差异只有
「目标文件 / 去重哨兵串 / 载荷块」三项 ⇒ 正是可参数化的那一类）。

用法：
    python _migrate/mem_append.py --list
    python _migrate/mem_append.py ws-migrate
    python _migrate/mem_append.py ws-classify --target "<path>"
    python _migrate/mem_append.py ws-handoff --dry-run

设计要点
- 三个子命令对应三段记忆块。**载荷文本原样保留**（历史记录不重写）。
- 幂等：以各自的 `sentinel` 判定；已存在则 SKIP 且退出码 0（与原脚本一致）。
- 退出码：0 = 写入成功或已存在；2 = 目标文件不存在；3 = 未知子命令。
- `--dry-run` 只报告将做什么，不写盘。
"""
import argparse
import io
import os
import sys

# 2026-09-16 那次会话的记忆文件（原三个脚本共用同一目标）
DEFAULT_TARGET = (r"C:\Users\<user>\host-workspace\2026-09-16-12-52-36"
                  r"\.workbuddy-ai\memory\2026-09-16.md")


# --------------------------------------------------------------------------
# 载荷块（原样搬运，勿改文字）
# --------------------------------------------------------------------------

BLOCK_WS_MIGRATE = """

## 项目迁移：host-workspace -> Desktop\\JVS（09-16 傍晚，已完成）

### 做了什么
把 M1 项目的 4 个目录按功能分区迁入 `C:\\Users\\<user>\\Desktop\\JVS`：
- `01-host-product`    <- `host-workspace\\2026-08-28-20-59-40`（宿主产品，**已是 git 仓库**，HEAD c6c87e9）
- `02-m1-evaluation`   <- `host-workspace\\outputs\\*`（22 个子项）
- `03-d10-workspace`   <- `host-workspace\\2026-09-08-20-30-19`
- `04-restricted-materials` <- `host-workspace\\restricted-review`（**涉敏，不得外发**）
合计 21,296 文件 / 3.81 GB。

### 关键手法
**真实字节移动 + 原路径留 Windows Junction**（共 24 个：顶层 3 + outputs 子项 22）。
工作区有 48,424 处硬编码绝对路径，靠 junction 透明解析，**零代码改动、完全可回滚**。

### 两个重要坑（下次直接复用经验）
1. **`outputs` 目录本身无法 rename**：宿主进程持有其句柄 -> WinError 5。
   解法：逐子项搬迁 + 子项级 junction，保留 `outputs` 实体目录。
   `outputs` 下唯一未迁的是文件 `m1-10-source-decisions-20260907-pending.md`。
2. **ctypes `GetFileAttributesW` 必须声明 `restype = c_uint32`**，否则返回值被当有符号 int，
   `-1 != 0xFFFFFFFF` 导致「路径不存在」被误判为「已存在」。

### 校验结论
- 9 个关键文件 SHA 迁移前后完全一致（台账 / 校验器 / D10 交付物 / 立项书）
- 顶层 + 子项 junction 全部可达；`outputs` 原路径读写删正常
- 文件数 21,296 -> 21,300（+4 为 `JVS\\_migrate` 新增脚本）

### 一个迁移无关的既有问题（需后续修）
`test_review_scoped_validator` 31 项测试中 **2 项 FAIL**，与迁移无关：
`blocked-authorization.json`（2026-09-15 项目方授权）把 batch-170/171/219-232
从 BLOCKED 豁免，而 `test_payload_cannot_point_to_forbidden_metadata_or_batch`
仍按旧预期断言 -> **测试用例需更新**。

### 交付文档
- `Desktop\\JVS\\README.md` —— 分区导航 + D10 状态 + 三个已知坑 + 受限素材警告
- `Desktop\\JVS\\MIGRATION-RECORD.md` —— 迁移前后对照、Junction 清单、S5 校验、回滚方式
- 脚本：`JVS\\_migrate\\{s2s4_migrate2,move_outputs_children,s5_verify}.py`
"""

BLOCK_WS_CLASSIFY = """

## JVS 按项目功能做中文分类（09-16 傍晚，已完成）

### 关键判断：功能分类做成**索引层**，不动物理文件
勘察发现两个全系统引用锚点，改名/移动即破坏引用：
- `01-host-product\\2026-08-28-20-59-40\\docs\\host_memory_dump\\`
  被 **194** 个文件引用（其中 59 代码 / 70 数据）；
  且 `docs\\host_memory_dump` 以**裸相对段**出现 **132 处**，按 CWD 解析
- `03-d10-workspace\\2026-09-08-20-30-19\\`
  被 **1,200** 个文件引用（其中 225 代码）；265 个脚本**同目录互相 import**

=> 所以功能分类以 `_index/` 叠加标注实现，物理层 0 改动，junction 24 个全部保持有效。

### 十大功能类（21,115 文件，0 未归类）
| 大类 | 定位 | 文件数 |
|---|---|---|
| 01 产品定义 | 这个 Agent 要做什么 | 53 |
| 02 技术实现 | 提示词 + 因果引擎/评测/采集三套工具 | 84 |
| 03 语料与图谱 | 素材/事件库/边库/题集gold/图谱定版 | 145 |
| 04 评测与实验 | 基线变体/报告/中间产物 | 365 |
| 05 治理与决策 | D09/D10/证据治理/来源链/定向重跑 | 186 |
| 06 抽取执行 | 全量100payload/试点/合成语料 | 15,467 |
| 07 语义复核机械 | 修订层/快照/校验器/台账 | 4,737 |
| 08 桥接与协作 | DSH 桥接 | 6 |
| 09 工程与运维 | 迁移索引/导航/仓库配置/审计 | 38 |
| 10 受限素材 | D09样本 + D10语料 G01–G17 | 34 |

### 交付物
- `Desktop\\JVS\\_index\\功能分类总表.md`（10 大类 × 40+ 子类，含代表文件）
- `Desktop\\JVS\\_index\\文件索引.jsonl`（21,115 行 `{p,l1,l2,s}`）
- `Desktop\\JVS\\_index\\分类统计.json`
- 分类规则：`Desktop\\JVS\\_migrate\\classify.py`（可改后重跑）
- 总表生成器：`Desktop\\JVS\\_migrate\\make_index_md.py`
- `Desktop\\JVS\\README.md` 已加入「功能索引层」与「为什么不能改建文件夹」两节

### 踩到的坑（写规则时）
- Python 字符串里 `r"...\\\\"` 是**字面双反斜杠**，导致 `host_memory_dump` 匹配全败（404 条落进未归类）
- 规则**顺序即优先级**：`06 工作区抽取暂存` 若排在 `07 全量复核修订层` 前，会把 3,600+ 复核产物吞掉
- `03\\outputs` 真实构成：`d10-synthetic-*` 7,948 / `full-review*` 3,638 / `d10-relocation-*` 329 / `browser-review` 170
"""

BLOCK_WS_HANDOFF = """

## JVS 接手包 + 功能进度标注（09-16 傍晚，已完成）

### 一、接手包 `_handoff/`（新会话入口）
目标是「其他对话接手就能直接用」。产出：
- `HANDOFF.md` —— 接手总览：30 秒读懂项目、接手第一步、东西在哪、项目全貌、
  当前主线、安全红线、常见坑、工作纪律、文件地图、**「如果只剩一件事」**
- `PENDING.md` —— 待办清单 + 已知缺陷（6 项缺陷表）
- `env/ENV.md` —— 环境契约：解释器、凭证（RELAY_KEY 不落盘）、LLM 通道
  （SenseNova 中转 glm-5.2；deepseek-v4-flash TPM 耗尽勿用）、
  环境变量全表、外部依赖、**2 个已知风险**、运行铁律、隐私闸门
- `env/RUN-CONTRACT.md` —— **运行目录契约**（关键）：
  03 工作区 **178 个脚本**引用 CWD 相对路径 `outputs/full-review`，
  且混用老绝对路径（靠 Junction 生效）→ **必须在 03 目录内运行**
- `env/check_env.sh` —— **环境自检脚本**（实测 20 OK / 1 WARN / 0 FAIL）：
  解释器、6 个关键脚本可编译、离线回归、数据资产、凭证闸门、外部依赖、**Junction 完整性**
- `scripts/SCRIPTS.md` —— 脚本登记表：15 个核心脚本（含分类/进度/是否联网/作用/用法）
  + 03 工作区 265 脚本按前缀分 10 族 + 离线 vs 联网速查
- `skills/` —— 技能分「项目专用」（d10-review-overlay-closeout）与
  「通用」（windows-junction-project-migration / launch-dsh / diagnose-subagent-failure）

### 二、工作区配置 `.workbuddy-ai/`
- `memory/MEMORY.md` —— 汇总项目长期记忆：因果层铁律（4 条）、
  **提示注入风险放大器三层防御**、明确不自研清单（召回管线/主动性/审批）、
  关键数字表、M1 三级验收、易复发事实陷阱、**标注了 1 处过期点**
  （原记忆写 v0.2 是现行版，实际已是 v0.3）
- `skills/` —— 4 个技能已装入，新会话自动可用

### 三、功能进度标注（用户要求：标在功能名后）
状态词表（用户指定）：**已开发 / 开发中 / 待开发 / 待规划**
- 定义在 `_migrate/status_map.py`，**依据一手证据非推测**
- 大类进度 = 子类中最未完成者（木桶口径）
- 生成器 `_migrate/make_progress_md.py` → `_index/功能进度表.md` + `_handoff/PENDING.md`
- `_index/功能分类总表.md` 已重写为带进度版（44 子类已开发 / 4 开发中）

### 四、查证出的关键进度事实（都是硬依据）
| 事实 | 依据 |
|---|---|
| M1 主验收 **21/30 = 70.00%** 已达标（门槛 18/30） | `M1-10-正式基线报告` |
| **编造边 0/67** 主判据通过（168 对抽检） | `dsh-takeover-20260902.md` |
| 复核台账 **233/233 单元、3732/3732 事件全闭环** | `acceptance-ledger.json` |
| **0 阻塞项**；123 条非阻塞提示（severity 全 low/minor/info） | 同上 open_findings 分析 |
| 验收等级：independent_accepted 89 / with_findings 139 / lead_accepted 5 | 同上 |
| 端到端合格率 **未评** | 立项书要求三项全报，仅两项有数据 |
| 阶段：M0 未正式做（链A PoC 替代部分）、**M1 开发中**、M2/M3 待规划 | `jarvis-agent-design-v0.3.md` §8 |

### 五、两个新发现的环境风险
1. **openclaw 与托管 node v22.22.2 不兼容**（要求 >=22.22.3）→ 用系统 node v24 或走 embedded fallback
2. 03 工作区脚本的**运行目录约束**（178 个 CWD 相对路径）——
   这是接手最容易踩的坑，单独立了 RUN-CONTRACT.md
"""


# --------------------------------------------------------------------------
# 子命令表：sentinel = 去重哨兵串（与旧脚本逐字一致）
# --------------------------------------------------------------------------

CASES = {
    "ws-migrate": {
        "sentinel": "项目迁移：host-workspace -> Desktop",
        "title": "项目迁移（host-workspace → Desktop\\JVS）",
        "block": BLOCK_WS_MIGRATE,
        "origin": "append_ws_memory.py",
    },
    "ws-classify": {
        "sentinel": "按项目功能做中文分类",
        "title": "按项目功能做中文分类",
        "block": BLOCK_WS_CLASSIFY,
        "origin": "append_ws_memory2.py",
    },
    "ws-handoff": {
        "sentinel": "JVS 接手包",
        "title": "接手包 + 功能进度标注",
        "block": BLOCK_WS_HANDOFF,
        "origin": "append_ws_memory3.py",
    },
}


def append_block(target, sentinel, block, dry_run=False):
    """幂等追加：已含 sentinel 则跳过。返回退出码。"""
    if not os.path.exists(target):
        print("MISS", target)
        return 2
    before = os.path.getsize(target)
    with io.open(target, "r", encoding="utf-8") as f:
        cur = f.read()
    if sentinel in cur:
        print("SKIP already present", before)
        return 0
    if dry_run:
        print("DRY-RUN would append %d chars -> %s (%d bytes)"
              % (len(block), target, before))
        return 0
    with io.open(target, "a", encoding="utf-8") as f:
        f.write(block)
    print("OK %d -> %d" % (before, os.path.getsize(target)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="工作区记忆块幂等追加（合并自 append_ws_memory{,2,3}.py）")
    ap.add_argument("case", nargs="?", help="子命令名（见 --list）")
    ap.add_argument("--target", default=DEFAULT_TARGET,
                    help="目标记忆文件；默认 %s" % DEFAULT_TARGET)
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写盘")
    ap.add_argument("--list", action="store_true", help="列出全部子命令后退出")
    args = ap.parse_args(argv)

    if args.list or not args.case:
        print("可用子命令（共 %d 个）：" % len(CASES))
        for name, c in CASES.items():
            print("  %-12s %-34s  ← %s" % (name, c["title"], c["origin"]))
        print("\n目标文件默认：%s" % DEFAULT_TARGET)
        return 0 if args.list else 3

    if args.case not in CASES:
        print("未知子命令：%s（用 --list 查看）" % args.case, file=sys.stderr)
        return 3

    c = CASES[args.case]
    return append_block(args.target, c["sentinel"], c["block"], args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
