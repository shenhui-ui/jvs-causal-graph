# 工作入口清单（ENTRYPOINTS）

> 建立 **2026-09-21**（对齐方案 P5；对应上游机制 **M12「生命周期命令层」**）。
>
> ⚠️ **本文件不是命令层，JVS 也没有命令层。**
> 上游用 9 个斜杠命令把六阶段做成入口（每个命令自动激活对应技能）；
> JVS 的等价物是**「文档入口 + 技能触发」** —— 靠**读对文档**与**技能被正确触发**进入流程。
> ⇒ 本文只登记「**想干 X，该去哪**」，**不得宣称实现了命令层**。
>
> **路径基准**：本文相对路径均相对 JVS 仓库根 `C:\Users\<user>\Desktop\JVS`。

## 0. 与上游 9 命令的映射

| 上游命令（六阶段） | JVS 等价入口 | 说明 |
|---|---|---|
| `/define`（立项 / 需求） | 立项书 + 决策记录 | `01-host-product/.../docs/M1-立项书-*.md`（已签字） |
| `/plan`（方案） | 方案文档 | 如 `DEV-STANDARDS-ALIGNMENT-PLAN.md` / `PHASE-C-PLAN.md` |
| `/build`（实现） | 实施 + 索引层六步 | 见 `_handoff/DEFINITION-OF-DONE.md` §2 |
| `/review`（评审） | **第二方复核** | 见 `_handoff/SECOND-PARTY-REVIEW.md` |
| `/verify`（验证） | 双门禁 + 集合差 + 收尾核验 | 见 `_handoff/DEFINITION-OF-DONE.md` §3 / §5 / §6 |
| `/ship`（发布） | ⛔ **不适用** | 本项目**永不配置远端**（`GIT-POLICY.md` §九） |
| `/handoff`（交接） | 交接三动作 | 见技能 `jvs-project-handoff` |
| `/debug`（排障） | 系统化调试 | 见技能 `systematic-debugging` |
| `/skill`（技能编写） | 技能编写与验证 | 见技能 `skill-authoring-and-verification` |

## 1. 「我想做 X」→ 去哪

| 我想… | 先读 | 触发技能 |
|---|---|---|
| 接手这个项目 | `_handoff/HANDOFF.md` | — |
| 知道还剩什么没做 | `_handoff/PENDING.md` | — |
| 落库一次改动 | `_handoff/DEFINITION-OF-DONE.md` | `jvs-index-layer-maintenance` |
| 安全操作 git（切分支 / 合并 / 还原） | `GIT-POLICY.md` §四 / §十一 / §十三 | `git-sigterm-safe-recovery` |
| 让结论有第二方验证 | `_handoff/SECOND-PARTY-REVIEW.md` | `diagnose-subagent-failure`（通道排查） |
| 写 / 改一份技能 | — | `skill-authoring-and-verification` |
| 排一个「没报错但结果不对」 | — | `systematic-debugging` |
| 判断某项能力要不要自研 | — | `capability-recon-before-build` |
| 把裁定落进证据治理包 | — | `jvs-governance-version-landing` |
| 回填受限素材复核 | — | `jvs-restricted-review-backfill` |
| 裁决因果边入库 | — | `jvs-causal-edge-arbitration` |
| 交接 / 新建接续会话 | — | `jvs-project-handoff` |
| 维护索引层产物 | `_index/` | `jvs-index-layer-maintenance` |
| 迁移项目目录 | — | `windows-junction-project-migration` |

## 2. ⚠️ 为什么不做真正的命令层

| 理由 | 依据 |
|---|---|
| 宿主**无斜杠命令设施** | 现状：JVS 工作流入口靠文档导航 |
| 上游命令会走 `git switch` / `git checkout` | ⛔ 本环境对这两个命令**必发 SIGTERM**，而 SIGTERM 会把工作区文件**批量移入回收站**（`GIT-POLICY.md` §十一 铁律 1）⇒ 照抄即事故 |
| 上游命令自动激活技能 | JVS 靠 **description 触发** ⇒ 由 `scripts/skills-gate.py` 的 `[5]` 段做静态自检（对齐方案 P3） |

⇒ **本清单只做「指路」，不做「执行」。**
若未来宿主提供命令设施，可在此基础上**升级**；当前**不得**以任何形式宣称已实现。
