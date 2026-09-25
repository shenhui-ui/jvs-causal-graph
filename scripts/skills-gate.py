#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""技能层机械校验（单一入口）—— 取代 `_handoff/skills/README.md` §维护 的四段手抄 bash。

为什么需要它（背景）
-------------------
四段复检（对齐核对 / 完整性核对 / 数量断言核查）原本全是 README 里的**手抄 bash**，
2026-09-21 实测出两处结构性缺陷：

  1. **假阳性** —— 第 4 关原用裸 `grep -c '&#x'`，对**记录该乱码形态的技能**误报
     （`skill-authoring-and-verification` 正文里「描述该乱码的那句话」与「检查命令本身」
     都命中 ⇒ 判「乱码 = 2」而文件完好）。这是「**子串假阳性**」家族在技能层的复现。
  2. **假阴性（更贵）** —— 手抄块 `for d in "$U"/*/`：**用户级根不存在时 glob 为空**，
     循环体一次都不进 ⇒ 打印「完好 0 份 ｜ 异常 0 份」⇒ **看起来全过**。
     这正是「**常量真探针不算探针**」的实例。

本项目自己的教条要求自动化它 —— `skill-authoring-and-verification` §八：
「**能用正则或校验脚本强制的机械约束 —— 那就去自动化，文档留给判断题**」。
⇒ 本脚本接管**机械**部分；README §维护 保留**判断题**部分（现象清单 / 权威判定顺序 / 修法）。

用法
----
    python scripts/skills-gate.py                     # 全量（默认逐份列出）
    python scripts/skills-gate.py --quiet             # 少打明细（失败项照打）
    python scripts/skills-gate.py --self-test         # 负向夹具自证（不碰真仓库）
    python scripts/skills-gate.py --user-root P --proj-root Q

退出码（**语义唯一**）
----------------------
    0 = 全过
    1 = 有阻塞项（含「判据源缺失」与「自检不一致」）
不存在「有输出即通过」；不存在第二套码。

输出标记（**刻意避开父门禁的三种专属标记**）
--------------------------------------------
`scripts/git-gate.sh` 的判据是「**自报 OK 数 == `grep -cE '\[OK\]'` 行数**」，
且其脚本内第 [4] 步注释明写：「**内嵌 Python 片段不得打印这三个前缀**」，
反面教材是「打印 16 行 / 计分 15」。
⇒ 本脚本一律用 `[通过]` / `[不通过]` / `[提示]` / `[自检]`，**绝不**输出
`[`+`OK`+`]`、`[`+`FAIL`+`]`、`[`+`WARN`+`]` 三个 token，以免污染父门禁计数。

路径来源
--------
项目级根**从本文件位置推导**（`__file__` → 仓库根 → `_handoff/skills`），
**不硬编码主工作树路径** —— 手抄块写死 `cd /c/Users/<user>/jvs-src`，
在 linked worktree 里跑会**检查错误的目录而不报错**。
用户级根默认 `~/.workbuddy-ai/skills`，可用 `JVS_SKILLS_USER_ROOT` 或 `--user-root` 覆盖。
"""

import argparse
import contextlib
import datetime
import io
import os
import re
import shutil
import sys
import tempfile

# ---------------------------------------------------------------- 常量

PROJ_SUBDIRS = ("项目专用", "通用")

# frontmatter：文件**必须**以 `---` 块开头，且块内含 name / description
FM_BLOCK = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
# 真乱码 = **实体 + hex + 分号** 三要素（裸 `&#x` 会误报「描述该乱码的文本」）
ENTITY = re.compile(r"&#x[0-9A-Fa-f]{2,6};")
# 「### 坑 N」条数（正文可能在 references/ ⇒ 必须扫整个技能目录）
PIT = re.compile(r"^### 坑 ", re.M)
# README 数量类断言
# ⚠️ 两处格式**必须从文件里抄，不能凭印象写**（README §维护 坑 9）：
#    ① 加粗在**外层** —— 是 `**共 18 份**（9 项目专用 + 9 通用）`，不是 `共 **18 份**`；
#    ② 量词在 `**` **之内** —— 是 `含**<中文数字>个**必踩的坑`，不是 `含**<中文数字>**个必踩的坑`。
# ⚠️ ② 里的**数字是活值**（技能每加一个坑就变）⇒ **不要把具体数字抄进注释**，一律现读：
#      grep -oE '含\*\*[一二三四五六七八九十两]+个\*\*必踩的坑' _handoff/skills/README.md | sort -u
#      （2026-09-21 索引技能加第 14 个坑后，本行原写死的「十三个」当场过期 —— 实测。）
#    凭印象写这两处会**静默命中 0**（本轮实测踩过，见技能 `skill-authoring-and-verification`）。
RE_TOTAL = re.compile(r"\*\*共\s*(\d+)\s*份\*\*\s*（\s*(\d+)\s*项目专用\s*\+\s*(\d+)\s*通用\s*）")
RE_REFS = re.compile(r"\*\*附页（`references/`）共\s*(\d+)\s*页\*\*：([^\n]*)")
RE_REF_PAIR = re.compile(r"`([^`]+)`\s*(\d+)")
RE_PIT_CLAIM = re.compile(r"含\*\*([一二三四五六七八九十两]+个)\*\*必踩的坑")
# 任意「数量类断言」形态（只为**列出**，供人工判断）
RE_CLAIM_ANY = re.compile(
    r"含\*\*[一二三四五六七八九十两]+个\*\*|已踩到\*\*[一二三四五六七八九十两]+处\*\*"
    r"|条硬防护|共\s*\*\*\d+\s*(?:份|项|页|处|条|个)")
RE_ENTRY = re.compile(r"^### `([^`]+)`")

# ---- P3（2026-09-21）：description 静态自检（SDO，对齐上游机制 M08）----
# 判据来源：技能 `skill-authoring-and-verification` §一「description 只写何时用」。
# 上限 1024 = 上游对 frontmatter **合计**的上限；本项目按 description 单字段判，更宽松。
DESC_MAX = 1024
# 触发式表述：description 必须含「何时用」的信号（中英双轨）
RE_TRIGGER = re.compile(
    r"(当.{0,30}(出现|需要|用户|提到|说|反馈|报告|要|做)"
    r"|时使用|使用[ 　—-]|use when|when to use|触发词)",
    re.I)
# 流程动词黑名单：出现即判「description 概括了流程」（SDO 反面）
# ⚠️ 只收**明确的流程信号**，不收单字「先」——「先查清…」在本项目是合法症状描述。
FLOW_BLACKLIST = (
    (r"第[一二三四五六七八九]步", "第N步"),
    (r"步骤\s*[1-9]", "步骤N"),
    (r"先.{0,15}再.{0,15}", "先…再…"),
    (r"然后", "然后"),
    (r"接着", "接着"),
    (r"(执行|运行)以下", "执行/运行以下"),
)


CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
            "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def cn2int(s):
    """中文数字 → int（覆盖 七 / 十 / 十一 / 十三 / 二十 等）。解析不了返回 None。

    先剥掉量词 —— README 的写法是 `含**<中文数字>个**必踩的坑`（**个在 `**` 之内**）。
    """
    s = re.sub(r"[个处条页项份]$", "", s or "")
    if not s:
        return None
    if s in CN_DIGIT:
        return CN_DIGIT[s]
    if "十" in s:
        a, _, b = s.partition("十")
        if a and a not in CN_DIGIT:
            return None
        if b and b not in CN_DIGIT:
            return None
        return (CN_DIGIT.get(a, 1) if a else 1) * 10 + (CN_DIGIT.get(b, 0) if b else 0)
    return None


# ---------------------------------------------------------------- 报告器

class Report:
    """收集判定项。通过 / 不通过 会被计数并参与自检。"""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.oks = []            # 通过消息（供夹具断言「某关**确实跑了**」）
        self.fails = []          # 失败消息（供夹具断言）
        self._ok_lines = 0
        self._bad_lines = 0

    def ok(self, msg):
        print("  [通过] %s" % msg)
        self.passed += 1
        self._ok_lines += 1
        self.oks.append(msg)

    def fail(self, msg):
        print("  [不通过] %s" % msg)
        self.failed += 1
        self._bad_lines += 1
        self.fails.append(msg)

    def note(self, msg):
        print("  [提示] %s" % msg)

    def detail(self, msg):
        print("         %s" % msg)

    def section(self, title):
        print("\n%s" % title)

    def selfcheck_ok(self):
        return self.passed == self._ok_lines and self.failed == self._bad_lines


# ---------------------------------------------------------------- 工具

def read_text(path):
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def tree(root):
    """{相对路径(/-分隔): 字节}；目录不存在返回 None。"""
    if not os.path.isdir(root):
        return None
    out = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d != "__pycache__"]
        for f in fn:
            fp = os.path.join(dp, f)
            rel = os.path.relpath(fp, root).replace(os.sep, "/")
            with open(fp, "rb") as fh:
                out[rel] = fh.read()
    return out


def tree_diff(a, b):
    """逐文件比较两份目录，返回差异描述（空 = 完全一致）。含 references/ 递归。"""
    msgs = []
    for k in sorted(set(a) | set(b)):
        if k not in a:
            msgs.append("仅项目级有 %s" % k)
        elif k not in b:
            msgs.append("仅用户级有 %s" % k)
        elif a[k] != b[k]:
            msgs.append("内容不同 %s" % k)
    return msgs


def list_skills(root):
    """列出技能目录名（排除 `_bm_*` 与隐藏项）；根不存在返回 None。"""
    if not os.path.isdir(root):
        return None
    return sorted(d for d in os.listdir(root)
                  if os.path.isdir(os.path.join(root, d))
                  and not d.startswith("_bm_") and not d.startswith("."))


def _walk_files(skill_dir):
    for dp, dn, fn in os.walk(skill_dir):
        dn[:] = [d for d in dn if d != "__pycache__"]
        for f in fn:
            yield os.path.join(dp, f)


def entity_hits(skill_dir):
    """整个技能目录（含 references/）里的真乱码实体命中数。"""
    n = 0
    for fp in _walk_files(skill_dir):
        t = read_text(fp)
        if t:
            n += len(ENTITY.findall(t))
    return n


def pit_count(skill_dir):
    n = 0
    for fp in _walk_files(skill_dir):
        t = read_text(fp)
        if t:
            n += len(PIT.findall(t))
    return n


def frontmatter_state(skill_dir):
    """三关之一。返回 (ok:bool, 短说明)。"""
    t = read_text(os.path.join(skill_dir, "SKILL.md"))
    if t is None:
        return False, "SKILL.md缺失"
    m = FM_BLOCK.match(t)
    if not m:
        return False, "缺失(未以---块开头)"
    missing = [k for k in ("name:", "description:") if k not in m.group(1)]
    if missing:
        return False, "缺" + "/".join(k.rstrip(":") for k in missing)
    return True, "ok"


def fm_name(skill_dir):
    t = read_text(os.path.join(skill_dir, "SKILL.md"))
    if not t:
        return None
    m = FM_BLOCK.match(t)
    if not m:
        return None
    mm = re.search(r"^name:\s*(\S+)\s*$", m.group(1), re.M)
    return mm.group(1) if mm else None


def fm_description(skill_dir):
    t = read_text(os.path.join(skill_dir, "SKILL.md"))
    if not t:
        return None
    m = FM_BLOCK.match(t)
    if not m:
        return None
    mm = re.search(r"^description:[ \t]*(.+?)[ \t]*$", m.group(1), re.M)
    return mm.group(1) if mm else None


def desc_state(skill_dir):
    """P3 静态自检。返回 (ok:bool, 短说明)。

    三关：长度 ≤ DESC_MAX / 含触发式表述 / 不含流程动词黑名单。
    ⚠️ 负向触发用例（「该触发时没触发」）依赖子代理设施 ⇒ **本轮不做**。
    """
    d = fm_description(skill_dir)
    if d is None:
        return False, "无 description"
    if len(d) > DESC_MAX:
        return False, "长度 %d > %d" % (len(d), DESC_MAX)
    if not RE_TRIGGER.search(d):
        return False, "无触发式表述（缺「当…时使用」类信号）"
    for rx, label in FLOW_BLACKLIST:
        if re.search(rx, d):
            return False, "含流程动词「%s」（SDO：只写何时用）" % label
    return True, "%d 字符" % len(d)


def proj_dir_of(proj_root, name):
    for sub in PROJ_SUBDIRS:
        cand = os.path.join(proj_root, sub, name)
        if os.path.isdir(cand):
            return cand
    return None


# ---- 第三处（宿主约定路径，**不是**权威副本）：仓内 `.workbuddy-ai/skills/` ----
# ⭐ 2026-09-25 用户裁定：「改为指针 README + 纳入本脚本校验面」。
#
# 为什么要有这一段：该目录是**宿主的项目级技能约定路径**，与 `_handoff/skills/`（本仓库的
# 权威项目级副本）**不是同一套**。2026-09-25 实测它有 4 份技能，其中 **2 份与权威副本不一致**
# ⇒ 静默过期的**第二真值源**。`DEFINITION-OF-DONE.md` §8 只定义两副本，故此前它「既不在纪律内、
# 也不在机械校验内」。
#
# 判据（fail-closed，三条缺一不可）：
#   ① 该目录**必须存在**；
#   ② 必须存在**指针 README**，且**自述权威副本位置**（含关键词 `POINTER_MARK`）；
#   ③ **不得含任何 `*/SKILL.md`** —— 一旦出现即说明又有人往这里塞技能。
# ⚠️ ③ 是**防回归**判据（改后当前状态必过）；为证明它**能拒绝**而非恒真，
#    `--self-test` 里配了「往第三处塞一个 `SKILL.md`」的负向夹具。
THIRD_REL = os.path.join(".workbuddy-ai", "skills")
POINTER_MARK = "权威副本"


def third_root_default():
    """默认第三处 = 本脚本所在仓库根下的 `.workbuddy-ai/skills`。"""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        THIRD_REL)


def third_offenders(root):
    """第三处里出现的 `*/SKILL.md` 子目录名（排序）。"""
    out = []
    if not os.path.isdir(root):
        return out
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        d = os.path.join(root, name)
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, "SKILL.md")):
            out.append(name)
    return out


# ---------------------------------------------------------------- 主检查

def run_checks(user_root, proj_root, quiet=False, user_root_explicit=False,
               third_root=None):
    """四段判据合一（2026-09-25 起为五段，含 [6] 第三处）。返回 Report。"""
    r = Report()
    if third_root is None:
        third_root = third_root_default()
    skills = list_skills(user_root)
    proj_names = []
    for sub in PROJ_SUBDIRS:
        got = list_skills(os.path.join(proj_root, sub))
        if got:
            proj_names.extend(got)
    proj_names = sorted(proj_names)
    user_missing = skills is None
    proj_missing = not os.path.isdir(proj_root)

    # ---------- [0] 判据源在位（反假绿：缺根不得静默放行） ----------
    r.section("[0] 判据源在位")
    if user_missing:
        r.fail("用户级根不存在或不可读: %s" % user_root)
        r.detail("手抄块在这种情形下会打印「完好 0 份 ｜ 异常 0 份」"
                 "（glob 为空 ⇒ 循环体不进）—— 那是假绿，本脚本判阻塞。")
    elif not skills:
        r.fail("用户级根存在但一个技能都没有: %s" % user_root)
    else:
        r.ok("用户级根可读: %d 份技能" % len(skills))
    if proj_missing:
        r.fail("项目级根不存在或不可读: %s" % proj_root)
    elif not proj_names:
        r.fail("项目级根存在但一个技能都没有: %s" % proj_root)
    else:
        r.ok("项目级根可读: %d 份技能" % len(proj_names))
    if user_root_explicit and user_missing:
        r.note("--user-root 是显式指定的 ⇒ 根缺失一律判阻塞（反假绿），"
               "即使默认根缺失在本机之外属正常。")

    expected = skills if skills else proj_names

    # ---------- [1] 对齐核对：清单 ----------
    r.section("[1] 双副本对齐 —— 清单")
    if user_missing or proj_missing:
        for s in expected:
            r.fail("双副本清单核对不可执行: %s（一侧判据源缺失）" % s)
    else:
        only_u = sorted(set(skills) - set(proj_names))
        only_p = sorted(set(proj_names) - set(skills))
        for s in only_u:
            r.fail("只在用户级: %s" % s)
        for s in only_p:
            r.fail("只在项目级: %s" % s)
        if not only_u and not only_p:
            r.ok("清单一致（用户级 %d 份 = 项目级 %d 份）"
                 % (len(skills), len(proj_names)))
        r.note("项目级分布: " + " / ".join(
            "%s %d" % (sub, len(list_skills(os.path.join(proj_root, sub)) or []))
            for sub in PROJ_SUBDIRS))

    # ---------- [2] 对齐核对：逐份内容 ----------
    r.section("[2] 双副本对齐 —— 逐份内容（含 references/ 递归）")
    if user_missing or proj_missing:
        for s in expected:
            r.fail("逐份内容核对不可执行: %s（一侧判据源缺失）" % s)
    else:
        for s in skills:
            d = proj_dir_of(proj_root, s)
            if d is None:
                r.fail("逐份内容: %s（项目级无对应目录）" % s)
                continue
            diffs = tree_diff(tree(os.path.join(user_root, s)), tree(d))
            if diffs:
                r.fail("内容不一致: %s（%d 处）" % (s, len(diffs)))
                if not quiet:
                    for m in diffs[:8]:
                        r.detail(m)
                    if len(diffs) > 8:
                        r.detail("...另有 %d 处" % (len(diffs) - 8))
            else:
                r.ok("内容一致: %s" % s)

    # ---------- [3] 完整性三关 ----------
    r.section("[3] 完整性三关（frontmatter / 乱码实体 / 双副本）")
    if user_missing:
        for s in expected:
            r.fail("完整性核对不可执行: %s（用户级判据源缺失）" % s)
    else:
        for s in skills:
            ud = os.path.join(user_root, s)
            fm_ok, fm_msg = frontmatter_state(ud)
            ent = entity_hits(ud)
            d = proj_dir_of(proj_root, s)
            same = d is not None and not tree_diff(tree(ud), tree(d))
            if fm_ok and ent == 0 and same:
                r.ok("三关: %s (frontmatter=ok 乱码=0 双副本=ok)" % s)
            else:
                r.fail("三关: %s (frontmatter=%s 乱码=%d 双副本=%s)"
                       % (s, "ok" if fm_ok else fm_msg, ent,
                          "ok" if same else "不一致或缺失"))
            nm = fm_name(ud)
            if nm and nm != s:
                r.note("技能名与目录名不符: %s（frontmatter name=%s）" % (s, nm))

    # ---------- [4] 数量断言核查 ----------
    r.section("[4] 数量断言核查")
    readme = os.path.join(proj_root, "README.md")
    text = read_text(readme)
    if text is None:
        r.fail("数量断言判据源缺失: %s" % readme)
        text = ""

    # ③ 总份数
    m = RE_TOTAL.search(text)
    if not m:
        r.fail("README 未找到「共 N 份（A 项目专用 + B 通用）」断言")
    else:
        claimed, ca, cb = int(m.group(1)), int(m.group(2)), int(m.group(3))
        act_a = len(list_skills(os.path.join(proj_root, "项目专用")) or [])
        act_b = len(list_skills(os.path.join(proj_root, "通用")) or [])
        if (claimed, ca, cb) == (act_a + act_b, act_a, act_b):
            r.ok("总份数 %d == %d 项目专用 + %d 通用（README 断言）"
                 % (claimed, ca, cb))
        else:
            r.fail("总份数断言过期: README 写 %d（%d+%d），实测 %d（%d+%d）"
                   % (claimed, ca, cb, act_a + act_b, act_a, act_b))

    # ④ 附页页数
    m = RE_REFS.search(text)
    if not m:
        r.fail("README 未找到「附页（references/）共 N 页」断言")
    else:
        claimed, tail = int(m.group(1)), m.group(2)
        act = {}
        for sub in PROJ_SUBDIRS:
            base = os.path.join(proj_root, sub)
            if not os.path.isdir(base):
                continue
            for s in os.listdir(base):
                rd = os.path.join(base, s, "references")
                if os.path.isdir(rd):
                    act[s] = sum(len(fn) for _, _, fn in os.walk(rd))
        if claimed != sum(act.values()):
            r.fail("附页页数断言过期: README 写 %d 页，实测 %d 页"
                   % (claimed, sum(act.values())))
        else:
            r.ok("附页合计 %d 页（README 断言 == 实测）" % claimed)
        pairs = dict(RE_REF_PAIR.findall(tail))
        for name, num in RE_REF_PAIR.findall(tail):
            got = act.get(name)
            if got is None:
                r.fail("附页断言指向不存在的技能: %s" % name)
            elif got != int(num):
                r.fail("附页断言过期: %s README 写 %s 页，实测 %d 页" % (name, num, got))
            else:
                r.ok("附页断言: %s %s 页 == 实测" % (name, num))
        for name in sorted(act):
            if name not in pairs:
                r.fail("有附页但 README 未登记: %s（实测 %d 页）" % (name, act[name]))

    # ① + ② 坑数：**按条目归属**解析（断言行本身不含技能名，归属取上方 `### ` 标题）
    i0 = text.find("## 项目专用")
    i1 = text.find("## 维护")
    region = text[i0:i1] if (i0 >= 0 and i1 > i0) else ""
    actual_pits = {}
    if skills:
        for s in skills:
            c = pit_count(os.path.join(user_root, s))
            if c:
                actual_pits[s] = c
    cur = None
    checked = 0
    listed = []
    for line in region.splitlines():
        mh = RE_ENTRY.match(line)
        if mh:
            cur = mh.group(1)
            continue
        if RE_CLAIM_ANY.search(line):
            mp = RE_PIT_CLAIM.search(line)
            if mp and cur:
                num = cn2int(mp.group(1))
                if num is None:
                    listed.append(line.strip())
                    continue
                checked += 1
                got = actual_pits.get(cur, 0)
                if got == num:
                    r.ok("坑数断言: %s %d == 条目声称 %d" % (cur, got, num))
                else:
                    r.fail("坑数断言过期: %s README 写 %d，实测 %d" % (cur, num, got))
            else:
                listed.append(line.strip())
    if checked == 0:
        r.note("条目区内未解析到「含 N 个必踩的坑」型断言（0 条）")
    if listed:
        r.note("数量类断言未机械判定（需人工判断）%d 条:" % len(listed))
        for line in listed:
            r.detail(line[:100])
    if actual_pits and not quiet:
        r.note("坑数现数（供人工比对）: "
               + " / ".join("%s=%d" % (k, v) for k, v in sorted(actual_pits.items())))

    # 现象清单（§维护 内的历史记录）—— 列出但不判定
    if i1 > 0:
        hstart = text[:i1].count("\n") + 1
        hist = [(ln, ln_line.strip())
                for ln, ln_line in enumerate(text.splitlines(), 1)
                if ln > hstart and RE_CLAIM_ANY.search(ln_line)]
        if hist and not quiet:
            r.note("现象清单 %d 条（§维护 内的历史记录，不参与判定）" % len(hist))
            for ln, line in hist:
                r.detail("README:%d  %s" % (ln, line[:96]))
    # ---------- [5] description 静态自检（SDO，P3 于 2026-09-21 接入） ----------
    r.section("[5] description 静态自检（SDO）")
    if user_missing:
        for s in expected:
            r.fail("description 自检不可执行: %s（用户级判据源缺失）" % s)
    else:
        for s in skills:
            ok, why = desc_state(os.path.join(user_root, s))
            if ok:
                r.ok("description: %s（%s）" % (s, why))
            else:
                r.fail("description 不合 SDO: %s（%s）" % (s, why))
        r.note("判据：长度 ≤ %d / 含触发式表述 / 不含流程动词黑名单；"
               "**负向触发用例依赖子代理设施 ⇒ 本轮不做**" % DESC_MAX)

    # ---------- [6] 第三处（仓内 `.workbuddy-ai/skills/`）—— 2026-09-25 新增 ----------
    r.section("[6] 第三处技能副本（仓内 `.workbuddy-ai/skills/`，只允许指针 README）")
    if not os.path.isdir(third_root):
        r.fail("第三处目录不存在: %s" % third_root)
        r.detail("它是宿主的项目级技能约定路径；本仓库用它放**指针 README**。"
                 "缺失 ⇒ 判阻塞（fail-closed，与 [0] 段同构）。")
    else:
        r.ok("第三处目录在位")
        ptr = os.path.join(third_root, "README.md")
        txt = read_text(ptr)
        if txt is None:
            r.fail("第三处缺指针 README: %s" % ptr)
        elif POINTER_MARK not in txt:
            r.fail("第三处 README 未自述权威副本位置（缺关键词「%s」）" % POINTER_MARK)
        else:
            r.ok("第三处指针 README 在位且自述权威位置")
        off = third_offenders(third_root)
        if off:
            r.fail("第三处出现技能副本（此处不得放技能）: %s" % ", ".join(off))
            r.detail("权威副本只有两处 —— 用户级 `~/.workbuddy-ai/skills/` 与项目级 "
                     "`_handoff/skills/`（见 `_handoff/DEFINITION-OF-DONE.md` §8）。"
                     "第三处一旦放技能即形成**第二真值源**（2026-09-25 实测曾有 4 份、其中 2 份已过期）。")
        else:
            r.ok("第三处无技能副本（0 个 `*/SKILL.md`）")

    return r


# ---------------------------------------------------------------- 负向夹具自证

FM_GOOD = "---\nname: %s\ndescription: 当出现夹具场景时使用。\n---\n\n# 夹具\n\n正文。\n"
# P3 夹具：description 三关各一个坏样本（好样本见 FM_GOOD，其 description 已含触发词）
FM_DESC_LONG = ("---\nname: %s\ndescription: 当出现夹具场景时使用。" + "长" * 1100
                + "\n---\n\n# 夹具\n\n正文。\n")
FM_DESC_NOTRIG = "---\nname: %s\ndescription: 夹具。\n---\n\n# 夹具\n\n正文。\n"
FM_DESC_FLOW = ("---\nname: %s\ndescription: 当出现夹具场景时使用，先查三层再决定自研。\n"
                "---\n\n# 夹具\n\n正文。\n")

FM_NONE = "# 没有 frontmatter 的技能\n\n正文。\n"
FM_ENTITY = "---\nname: %s\ndescription: 当出现夹具场景时使用。\n---\n\n# 夹具\n\n乱码 &#x4F46; 在这里。\n"
DESCRIBE_ENTITY = ("\n判据是 `grep -cE '&#x[0-9A-Fa-f]{2,6};'`，"
                   "裸 `&#x` 会误报「描述它的文本」。\n")


def _mk(parent, name, body):
    d = os.path.join(parent, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write(body)
    return d


def self_test(quiet=False):
    """用「已知好 / 已知坏」夹具自证判据有区分力。返回 Report。"""
    r = Report()
    tmp = tempfile.mkdtemp(prefix="jvs-skills-gate-fixture-")
    try:
        ur = os.path.join(tmp, "user")
        pr = os.path.join(tmp, "proj")
        pg = os.path.join(pr, "通用")
        os.makedirs(ur, exist_ok=True)
        os.makedirs(pg, exist_ok=True)

        # 好样本
        _mk(ur, "good-a", FM_GOOD % "good-a")
        _mk(pg, "good-a", FM_GOOD % "good-a")
        # 坏 1：frontmatter 被整段剥掉（2026-09-19 实测过的真实故障形态）
        _mk(ur, "bad-frontmatter", FM_NONE)
        _mk(pg, "bad-frontmatter", FM_NONE)
        # 坏 2：正文含**真**乱码实体
        _mk(ur, "bad-entity", FM_ENTITY % "bad-entity")
        _mk(pg, "bad-entity", FM_ENTITY % "bad-entity")
        # 坏 3：双副本内容不一致
        _mk(ur, "bad-content", FM_GOOD % "bad-content")
        _mk(pg, "bad-content", (FM_GOOD % "bad-content") + "\n用户级多一行。\n")
        # 坏 4：只在用户级
        _mk(ur, "only-user", FM_GOOD % "only-user")
        # **回归**：描述该乱码的技能 —— 不得被误报（假阳性回归夹具）
        _mk(ur, "describe-entity", (FM_GOOD % "describe-entity") + DESCRIBE_ENTITY)
        _mk(pg, "describe-entity", (FM_GOOD % "describe-entity") + DESCRIBE_ENTITY)
        # P3 负向夹具：description 三关（长度 / 触发式 / 流程黑名单）
        for nm in ("bad-desc-long", "bad-desc-notrig", "bad-desc-flow"):
            body = {"bad-desc-long": FM_DESC_LONG,
                    "bad-desc-notrig": FM_DESC_NOTRIG,
                    "bad-desc-flow": FM_DESC_FLOW}[nm] % nm
            _mk(ur, nm, body)
            _mk(pg, nm, body)

        # 数量断言夹具：正文含 2 条「### 坑 N」，README 条目声称 2 ⇒ 断言必须被**真跑到**
        pit_body = (FM_GOOD % "pit-skill") + "\n### 坑 1 甲\n\n甲。\n\n### 坑 2 乙\n\n乙。\n"
        _mk(ur, "pit-skill", pit_body)
        _mk(pg, "pit-skill", pit_body)

        with open(os.path.join(pr, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("# 夹具\n\n"
                     "> **共 9 份**（0 项目专用 + 9 通用）\n"
                     "> **附页（`references/`）共 0 页**：\n\n---\n\n"
                     "## 项目专用\n\n"
                     "### `pit-skill`\n\n含**两个**必踩的坑：\n\n"
                     "## 维护\n")

        # ---- 第三处（[6] 段）夹具：1 个「好」+ 3 个「坏」 ----
        # 好：目录在 + 指针 README 自述权威位置 + 无技能副本
        third_good = os.path.join(tmp, "third-good")
        os.makedirs(third_good, exist_ok=True)
        with open(os.path.join(third_good, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("# 指针\n\n权威副本在别处，此处不得放技能。\n")
        # 坏 A：塞了一个技能副本（正是 2026-09-25 处置掉的形态）
        third_skill = os.path.join(tmp, "third-skill")
        os.makedirs(third_skill, exist_ok=True)
        with open(os.path.join(third_skill, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("# 指针\n\n权威副本在别处。\n")
        _mk(third_skill, "stale-copy", FM_GOOD % "stale-copy")
        # 坏 B：指针 README 缺关键词
        third_nomark = os.path.join(tmp, "third-nomark")
        os.makedirs(third_nomark, exist_ok=True)
        with open(os.path.join(third_nomark, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("# 这里放技能\n\n随便写点。\n")
        # 坏 C：目录缺失（下面用不存在的路径直接构造）

        # 夹具轮的**详细输出被吞掉**（只留断言行），否则自证报告被 4 份子报告淹没
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            rep = run_checks(ur, pr, quiet=True, third_root=third_good)

        def fails_for(name):
            return [m for m in rep.fails if name in m]

        cases = [
            ("good-a", None, False, "好样本必须全过"),
            ("bad-frontmatter", "frontmatter", True, "frontmatter 被剥必须报"),
            ("bad-entity", "乱码", True, "真乱码必须报"),
            ("bad-content", "内容不一致", True, "双副本内容不一致必须报"),
            ("only-user", "只在用户级", True, "只在用户级必须报"),
            ("describe-entity", None, False, "描述乱码的技能不得被误报（假阳性回归）"),
            ("bad-desc-long", "长度", True, "description 超长必须报"),
            ("bad-desc-notrig", "触发式", True, "description 无触发式表述必须报"),
            ("bad-desc-flow", "流程动词", True, "description 含流程动词必须报"),
        ]
        for name, needle, should_fail, why in cases:
            got = fails_for(name)
            if should_fail:
                if got and needle in " | ".join(got):
                    r.ok("夹具 %s → 已指名报出（含「%s」）" % (name, needle))
                elif got:
                    r.fail("夹具 %s → 报了但未指名到正确的关（需含「%s」，实为「%s」）"
                           % (name, needle, " | ".join(got)[:90]))
                else:
                    r.fail("夹具 %s → 漏报（%s）" % (name, why))
            else:
                if got:
                    r.fail("夹具 %s → 误报（%s）：%s" % (name, why, " | ".join(got)[:90]))
                else:
                    r.ok("夹具 %s → 未误报（%s）" % (name, why))

        # 关键：**判据「确实跑了」也要断言** —— 本轮实测踩过「正则写错 ⇒ 该关静默 0 条 ⇒
        # 看起来全过」的假绿（`含**<中文数字>个**必踩的坑` 里 `个` 在 `**` 之内）。
        if any("坑数断言: pit-skill" in m for m in rep.oks):
            r.ok("数量断言：坑数断言**确实被跑到**（pit-skill 2 == 声称 2）")
        else:
            r.fail("数量断言：坑数断言**静默跳过**（正则没命中夹具 ⇒ 该关等于没跑）")
        if any(m.startswith("总份数 ") for m in rep.oks):
            r.ok("数量断言：总份数断言确实被跑到")
        else:
            r.fail("数量断言：总份数断言静默跳过")
        if any(m.startswith("附页合计 ") for m in rep.oks):
            r.ok("数量断言：附页断言确实被跑到")
        else:
            r.fail("数量断言：附页断言静默跳过")

        # 反假绿 1：用户级根缺失 ⇒ 必须**全部判缺**，不得「0 份 ⇒ 全过」
        with contextlib.redirect_stdout(io.StringIO()):
            rep2 = run_checks(os.path.join(tmp, "nonexistent"), pr, quiet=True)
        if rep2.failed > 0:
            r.ok("反假绿: 用户级根缺失 ⇒ 判 %d 项阻塞（非「0 份 ⇒ 全过」）" % rep2.failed)
        else:
            r.fail("反假绿失败: 用户级根缺失时未判任何阻塞 —— 正是手抄块的假绿形态")
        # 反假绿 2：两侧都缺
        with contextlib.redirect_stdout(io.StringIO()):
            rep3 = run_checks(os.path.join(tmp, "nope-u"), os.path.join(tmp, "nope-p"),
                              quiet=True)
        if rep3.failed > 0:
            r.ok("反假绿: 两侧根都缺失 ⇒ 判 %d 项阻塞" % rep3.failed)
        else:
            r.fail("反假绿失败: 两侧根都缺失时未判阻塞")
        # 反假绿 3：空目录（存在但无技能）
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty, exist_ok=True)
        with contextlib.redirect_stdout(io.StringIO()):
            rep4 = run_checks(empty, pr, quiet=True)
        if rep4.failed > 0:
            r.ok("反假绿: 用户级根存在但无技能 ⇒ 判 %d 项阻塞" % rep4.failed)
        else:
            r.fail("反假绿失败: 空技能目录被当作全过")

        # ---- [6] 第三处：判据必须**能拒绝**（四条，含 3 个负向）----
        if any("第三处" in m for m in rep.fails):
            r.fail("第三处（好夹具）→ 误报：%s" % " | ".join(
                m for m in rep.fails if "第三处" in m)[:90])
        else:
            r.ok("第三处（好夹具）→ 未误报（指针 README 在位且无技能副本）")
        # 「判据确实跑了」
        if any("第三处无技能副本" in m for m in rep.oks):
            r.ok("[6] 第三处「无技能副本」判据**确实被跑到**")
        else:
            r.fail("[6] 第三处「无技能副本」判据静默跳过（等于没跑）")

        def third_fails(root):
            with contextlib.redirect_stdout(io.StringIO()):
                rr = run_checks(ur, pr, quiet=True, third_root=root)
            return [m for m in rr.fails if "第三处" in m]

        got = third_fails(third_skill)
        if got and "技能副本" in " | ".join(got):
            r.ok("第三处（塞了技能副本）→ 已指名报出：%s" % got[0][:70])
        else:
            r.fail("第三处（塞了技能副本）→ 漏报（防回归判据是恒真的！）：%s" % (got or "无失败项"))
        got = third_fails(third_nomark)
        if got and "未自述" in " | ".join(got):
            r.ok("第三处（README 缺关键词）→ 已指名报出：%s" % got[0][:70])
        else:
            r.fail("第三处（README 缺关键词）→ 漏报：%s" % (got or "无失败项"))
        got = third_fails(os.path.join(tmp, "third-missing"))
        if got and "不存在" in " | ".join(got):
            r.ok("第三处（目录缺失）→ 已指名报出：%s" % got[0][:70])
        else:
            r.fail("第三处（目录缺失）→ 漏报（fail-closed 失效）：%s" % (got or "无失败项"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return r


# ---------------------------------------------------------------- 入口

def main(argv=None):
    ap = argparse.ArgumentParser(description="技能层机械校验（单一入口）")
    ap.add_argument("--user-root", default=None,
                    help="用户级技能根（默认 ~/.workbuddy-ai/skills）")
    ap.add_argument("--proj-root", default=None,
                    help="项目级技能根（默认 <仓库>/_handoff/skills）")
    ap.add_argument("--third-root", default=None,
                    help="第三处根（默认 <仓库>/.workbuddy-ai/skills）；只允许指针 README")
    ap.add_argument("--quiet", action="store_true", help="少打明细（失败项照打）")
    ap.add_argument("--self-test", action="store_true",
                    help="跑负向夹具自证（不检查真仓库）")
    a = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if a.self_test:
        print("=" * 46)
        print(" 技能层机械校验 —— 负向夹具自证")
        print("=" * 46)
        return _finish(self_test(quiet=a.quiet), "夹具自证")

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proj_root = a.proj_root or os.path.join(repo, "_handoff", "skills")
    third_root = a.third_root or os.path.join(repo, THIRD_REL)
    explicit = a.user_root is not None
    user_root = (a.user_root
                 or os.environ.get("JVS_SKILLS_USER_ROOT")
                 or os.path.join(os.path.expanduser("~"), ".workbuddy-ai", "skills"))

    print("=" * 46)
    print(" 技能层机械校验  (%s)" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    print(" 用户级: %s" % user_root)
    print(" 项目级: %s" % proj_root)
    print(" 第三处: %s" % third_root)
    print("=" * 46)
    rep = run_checks(user_root, proj_root, quiet=a.quiet,
                     user_root_explicit=explicit, third_root=third_root)
    return _finish(rep, "技能层")


def _finish(rep, label):
    print("\n" + "=" * 46)
    print(" 结果: 通过=%d  不通过=%d" % (rep.passed, rep.failed))
    if rep.selfcheck_ok():
        print(" [自检] 自报通过数 == [通过] 行数（%d）—— 无重复计分" % rep.passed)
    else:
        print(" [自检] 不一致: 自报 通过=%d 不通过=%d，实际行数 通过=%d 不通过=%d"
              % (rep.passed, rep.failed, rep._ok_lines, rep._bad_lines))
    if rep.failed == 0 and rep.selfcheck_ok():
        print(" %s 全过" % label)
        print("=" * 46)
        return 0
    print(" 存在阻塞项 —— 先修复 [不通过] 项")
    print("=" * 46)
    return 1


if __name__ == "__main__":
    sys.exit(main())
