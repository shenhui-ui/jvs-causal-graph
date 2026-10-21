# OLD-ROOT-EXEMPTION —— 旧根路径引用豁免登记（路线 B 登记件）

> **性质**：登记件。**这不是「没问题」，是「已知依赖不稳定旧根 / 已知留痕」**——库内仍有
> 3,645 个被跟踪文件含旧根字面量（`C:\Users\<user>\host-workspace\`，2026-10-20 现测），
> 其中 S2（354 个脚本）依赖的旧根 Junction 的 OS 信任态**不稳定**（2026-09-21 实测不可用、
> 2026-10-20 现测恢复可达，见 §1）——**不承诺可重跑**；S3/S4 属「当时事实」，
> **永不改写**。按「改记录 = 篡改当时事实」纪律，如需新结果请**另立重跑**，不要改这些文件。
>
> **裁定**：2026-10-20 项目方裁定路线 **A-lite + B**（`NEXT-旧根脚本清扫立项-20261020.md`
> §4）：S1 活件已改写（见 §2）；S2/S3/S4 按本件登记不改；并授权 git-gate 新增
> 「旧根引用守卫」步（判据 = 新增/修改行命中路径形状即 FAIL，见 `scripts/git-gate.sh` [11]）。
>
> **边界**：冻结索引（R2=③ 已裁仅登记不改）、`04-restricted-materials/`、宿主 memory 日志、
> 留痕文档正文——本件不触发任何对它们的改写。

---

## 1. 事实基线（2026-10-20 现测，勿引用记忆值）

| 事实 | 现测值 |
|---|---|
| 旧根下 Junction 总数 | **25**（顶层 3 + `outputs\` 子项 22；`MIGRATION-RECORD` §2 记 24 为口径差异，现测以 25 为准） |
| Junction 信任状态 | **不稳定（双时点实测不一致）**：2026-09-21 全部 25 个被判「不受信任的装入点」（`WinError 448`，bash→python 与 PowerShell 两条独立路径一致，PENDING §四）；**2026-10-20 现测恢复可达**（bash `-d` 4/4 通、Python `listdir`/`open` 穿透全通）⇒ 任何依赖旧根的判据/脚本**结果随本机 OS 态摆动**，且克隆副本无 Junction ⇒ 以「旧根不可承诺」为运行前提 |
| 含旧根字面量的 tracked 文件 | **3,645 / 20,939**（全形态并集；口径细目见 §5） |
| 含完整旧根绝对路径的文件 | **532**（`C:\…\host-workspace` 三写法并集；与 2026-09-28 裁定件口径 ② 524 同量级、口径不同） |
| Junction src→dst 映射 | 25 条逐条现测落盘于 `_staging/old-root-census-20261020.json`（canonical 串 md5 = `f7b299812ffc0e3817382d2c03eee3e9`） |

> ⚠️ **前提变化登记（2026-10-20 执行轮实测）**：立项件 §1.2 引 2026-09-21 结论
> 「旧根已完全失效」作为现状；执行轮现测该态**已翻转恢复**。A-lite + B 路线不受影响
> （登记 + 守卫 + 现根判据在两种 OS 态下均成立），但**依据须如实改为「不稳定」**，
> 不得引用「已失效」当现状。

## 2. S1 活件（已改写 —— 本轮唯一写集）

| 文件 | 命中（改写前） | 处置 |
|---|---|---|
| `_handoff/env/check_env.sh` | §7 四条旧根路径检查（判据结果随 OS 信任态摆动：09-21 不可用、10-20 恢复；克隆副本无 Junction 必红） | **已改写**（2026-10-20）：改查现根四迁移目录，等价语义、判据稳定、克隆可移植；改后本机实测 `OK=20 WARN=1 FAIL=0`（改写前同日实测旧判据亦 4/4 可达 —— 改写依据是**稳定性与可移植性**，不是当日失败）。残留判定：`git grep -nE 'host-workspace[\\\\/]' -- _handoff/env/check_env.sh` ⇒ **0 命中** |

S1 名单确认法（为什么只有它）：门禁（`git-gate.sh` 全 10 步引用的脚本/配置）、回归
（`REG_TESTS` 3 件）、生成器（`_migrate/` 六步 5 件）、活流水线（`scripts/` 全部）逐件
与旧根命中名单交叉核对，**其余全部干净**；核对命令见 §6 第 4 条。

## 3. 活件中的「口径判据串」——登记不改（改了会破坏口径）

| 文件 | 命中 | 为什么不改 |
|---|---|---|
| `_migrate/make_progress_md.py` | `_OLD_ROOT` / `_OLD_ROOT_BS1`（拼装构造，源码无裸串） | 是 **PENDING §四 旧根计数的判据串**（`old_root_facts()` 读 HEAD 树计数）。改写它 = 把「统计旧根引用」改成「统计新根引用」，口径立刻失真。源码裸串禁令见该文件 L138 注释 |
| `_migrate/status_map.py` | 1 处 | 历史事实陈述串（regression 文档含旧根路径的警示文案），非路径解析 |

判定命令：`git grep -nE 'host-workspace[\\\\/]' -- _migrate/make_progress_md.py _migrate/status_map.py`
⇒ 命中的是判据/陈述串；两文件的**运行时路径解析**均不涉旧根（现测：六步生成器跑通）。

## 4. S2 历史一次性脚本（354 件）——「已知依赖不稳定旧根」

**现状**：产物已冻结、脚本不再重跑；脚本内旧根路径的可用性**随本机 OS 对 Junction 的
信任态摆动**（§1 双时点实测）。2026-10-20 抽样实测 2 件均可跑通
（`_migrate/repo_recon.py ws-links` rc=0、`_migrate/s5_verify.py` rc=0 —— 两件均只
**枚举/判 reparse 元数据**、不穿透读取；穿透读取类脚本在 09-21 态下即失败
`WinError 448`）。⇒ **不承诺可重跑**：要恢复稳定可重跑能力 ⇒ 走立项件路线 A
（批量前缀改写），须另行授权。

| 分组 | 件数 |
|---|---|
| `03-d10-workspace/2026-09-08-20-30-19/`（含子目录） | 254 |
| `02-m1-evaluation/outputs/` | 53 |
| `01-host-product/…/tools/`（causal 31 / eval 1 / host 2） | 34 |
| `_archive/`（superseded 两目录） | 8 |
| `_migrate/` 一次性件（`mem_append` `move_outputs_children` `repo_recon` `s2s4_migrate2` `s5_verify`） | 5 |

**逐件判定命令**（对任一 S2 件 `F.py`）：
`grep -nE 'host-workspace[\\\\/]' F.py`（指出依赖行）；
`python F.py`（结果依赖 OS 信任态：只枚举/判 reparse 的可跑通；穿透读取旧根的在
09-21 态下为路径类失败 `WinError 448`）。
全量清单：census JSON `files[]` 中 `class=S2` 的 354 条（§5）。

## 5. S3 产物 provenance（3,189 件）——「当时事实」，永不改写

`.json` 3,160 / `.jsonl` 1 / `.txt` 8 / `.html` 3 / `_archive` 冻结备份 17。
分布：`03-` 1,885｜`02-` 1,244｜`01-` 43｜`_archive` 17。
这些是「当时从哪读」的记录（含 5 个冻结索引 1,165 条 `payload_path`，R2=③ 已裁仅登记）。
**改写 = 篡改当时事实**；如需新路径 ⇒ 另立重跑。

## 6. S4 留痕文档与边界件（99 件）—— 不改写

- `.md` 留痕 88 件：`MIGRATION-RECORD.md`、`GIT-POLICY.md`、评审/裁定/交接文档、
  `outputs/` 内 RUNME/README 等（当时操作指引的原文）。
  ⚠️ 其中 `_handoff/env/RUN-CONTRACT.md` 的「老路径靠 Junction 仍有效」陈述**不可依赖**
  （Junction 信任态不稳定，§1 双时点实测）——按纪律**保留原文**，以本件为准。
- `_handoff/skills/` 4 件（活技能文档）：`d10-review-overlay-closeout` 的旧根工作区指针
  已在 `技能库审计-过期内容清单-20261007.md` 条目 9 登记，**提示词改动是另一道独立授权**，不在本件范围。
- `.workbuddy-ai/memory/` 7 件：宿主日志，边界内不触碰。

## 7. 重建 Junction 的前置（如确需旧根复活）

命令见 `MIGRATION-RECORD.md`（`rmdir` + `mklink /J`，管理员 cmd）。
⚠️ **信任态不由重建决定**：同一批 Junction 在 2026-09-21 被判不受信任、2026-10-20
又恢复可达（§1），**未实测重建与信任恢复的因果关系**——不要把「重建」或「重启」
当既定解；以「旧根可用性不承诺」为运行前提。

## 8. 复现命令集（现测口径；清单为 census JSON，勿手抄）

```bash
cd /c/Users/<user>/jvs-src
PY="${JVS_PY:-C:/Users/<user>/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe}"

# 1) 全量普查（逐件：路径 / 形态计数 / 分类 / tracked / junction 映射 + md5）
"$PY" -B _staging/old-root-census.py        # 产物: _staging/old-root-census-<当日>.json

# 2) 全形态并集计数（与 census files[] 总数互证）
git grep -l 'host-workspace' | wc -l            # ⇒ 3645

# 3) 分类件数（以 census JSON 为真值源）
"$PY" -B -c "import json,io,collections;d=json.load(io.open('_staging/old-root-census-20261020.json',encoding='utf-8'));print(collections.Counter(f['class'] for f in d['files']))"

# 4) S1 交叉核对（活件清单 × 旧根命中）
git grep -l 'host-workspace' -- 'scripts/' '.githooks/' '_migrate/make_index_md.py' '_migrate/classify.py'
#   ⇒ 仅命中 §3 两个登记件（口径串），即 S1 改写集只剩 check_env.sh

# 5) S1 残留判定（路径形状口径，与 git-gate [11] 同判据）
git grep -nE 'host-workspace[\\\\/]' -- _handoff/env/check_env.sh   # ⇒ 0

# 6) junction 现测（reparse tag 判定，注意 os.path.islink 对 Junction 恒 False）
"$PY" -B -c "import os,stat;p=r'C:\Users\<user>\host-workspace\2026-09-08-20-30-19';print(os.stat(p,follow_symlinks=False).st_reparse_tag==stat.IO_REPARSE_TAG_MOUNT_POINT, os.readlink(p))"
```

---

_本件由 2026-10-20 旧根清扫（A-lite + B）执行轮产出；立项与裁定见
`NEXT-旧根脚本清扫立项-20261020.md`。所有数字为当日 census 现测，引用前请按 §8 重测。_
