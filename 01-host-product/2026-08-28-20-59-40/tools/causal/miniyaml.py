# -*- coding: utf-8 -*-
"""极简 YAML 读取器（纯标准库，只支持本项目 `fallback.yaml` 用到的子集）。

为什么不用 PyYAML
-----------------
1. 本目录既有约定是「不依赖任何第三方库」（见 `causal_graph.py` 模块头）；
2. 本机 Python 环境**未安装** PyYAML（现测 `import yaml` ⇒ ModuleNotFoundError）。

支持的子集
----------
- 缩进嵌套（每层 **2 空格**）的映射 `key: value`
- 块列表 `- item`
- 行内列表 `[a, b, c]`
- 标量：字符串（可加单/双引号）、整数、浮点、`true`/`false`、`null`/`~`
- 行注释 `# ...`（整行，或以空白起头的行尾）

明确**拒绝**（抛 `ValueError`，绝不静默忽略 —— 判据纪律：**能拒绝才算校验**）
--------------------------------------------------------------
- 制表符缩进、非 2 倍数缩进
- 块标量 `|` / `>`、锚点 `&` / 别名 `*`、流式映射 `{...}`、多文档 `---` / `...`
- **重复键**（静默覆盖是本项目最危险的一类静默失效）
- 映射与列表混排、缺 `:`、空键、空标量

设计取舍
--------
宁可**窄而严**：只读得懂本项目自己写的 `fallback.yaml`，读不懂就报错。
若将来需要更完整的 YAML，应显式换成 PyYAML，而不是悄悄放宽本加载器。
"""

import io

# 这些行首字符代表本加载器不支持的 YAML 语法；遇到即拒绝。
_UNSUPPORTED_HEADS = ("|", ">", "&", "*", "{", "}", "---", "...", "!")


def _strip_comment(line):
    """去掉行注释，尊重引号内的 `#`。"""
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out).rstrip()


def _split_inline(text):
    """按逗号切分行内列表，尊重引号。"""
    parts, buf, quote = [], [], None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
            continue
        if ch == ",":
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return [p for p in (x.strip() for x in parts) if p != ""]


def _scalar(tok):
    """把一段文本解析成标量（含行内列表）。"""
    tok = tok.strip()
    if tok == "":
        raise ValueError("空标量")
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ('"', "'"):
        return tok[1:-1]
    if tok[0] in _UNSUPPORTED_HEADS:
        raise ValueError("不支持的 YAML 语法：%r" % tok)
    if tok.startswith("[") and tok.endswith("]"):
        inner = tok[1:-1].strip()
        return [_scalar(x) for x in _split_inline(inner)] if inner else []
    low = tok.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~"):
        return None
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        pass
    return tok


def _looks_like_mapping(body):
    """判断列表项 `- <body>` 是否是映射项（`- key: value`）。

    只认「键不含空格、且冒号后紧跟空格或行尾」的形式；
    这样 `- http://x` 这类含冒号的**标量**不会被误判成映射。
    """
    body = body.strip()
    if not body or body[0] in ('"', "'", "[", "{"):
        return False
    key, sep, rest = body.partition(":")
    if not sep:
        return False
    key = key.strip()
    if not key or " " in key or key[0] in _UNSUPPORTED_HEADS:
        return False
    return rest == "" or rest.startswith(" ")


def _parse_block(rows, pos, indent):
    """解析 rows[pos:] 中缩进恰为 `indent` 的块，返回 (value, next_pos)。"""
    if pos >= len(rows):
        return None, pos
    if rows[pos][1].startswith("- "):
        items = []
        while pos < len(rows):
            ind, text, ln = rows[pos]
            if ind < indent:
                break
            if ind > indent:
                raise ValueError("第 %d 行：列表项缩进异常" % ln)
            if not text.startswith("- "):
                raise ValueError("第 %d 行：列表中混入非列表项" % ln)
            body = text[2:].strip()
            pos += 1
            if _looks_like_mapping(body):
                # 映射项：把 `- key: value` 作为种子，其余键在 indent+2 处继续
                sub = [(indent + 2, body, ln)]
                while pos < len(rows) and rows[pos][0] > indent:
                    sub.append(rows[pos])
                    pos += 1
                value, subpos = _parse_block(sub, 0, indent + 2)
                if subpos != len(sub):
                    raise ValueError("第 %d 行：列表映射项未被解析" % sub[subpos][2])
                items.append(value)
            else:
                items.append(_scalar(body))
        return items, pos

    mapping = {}
    while pos < len(rows):
        ind, text, ln = rows[pos]
        if ind < indent:
            break
        if ind > indent:
            raise ValueError("第 %d 行：缩进异常（预期 %d 空格）" % (ln, indent))
        if text.startswith("- "):
            raise ValueError("第 %d 行：映射中混入列表项" % ln)
        if ":" not in text:
            raise ValueError("第 %d 行：缺少 ':'" % ln)
        key, _, rest = text.partition(":")
        key = key.strip()
        if not key:
            raise ValueError("第 %d 行：键为空" % ln)
        if key in mapping:
            raise ValueError("第 %d 行：重复键 %r" % (ln, key))
        rest = rest.strip()
        pos += 1
        if rest == "":
            if pos < len(rows) and rows[pos][0] > indent:
                value, pos = _parse_block(rows, pos, rows[pos][0])
            else:
                value = None
        else:
            value = _scalar(rest)
        mapping[key] = value
    return mapping, pos


def loads(text):
    """解析文本为 Python 对象。"""
    rows = []
    for i, raw in enumerate(text.split("\n"), 1):
        if "\t" in raw:
            raise ValueError("第 %d 行含制表符：本加载器不支持 Tab 缩进" % i)
        s = _strip_comment(raw)
        if not s.strip():
            continue
        indent = len(s) - len(s.lstrip(" "))
        if indent % 2 != 0:
            raise ValueError("第 %d 行缩进 %d 非 2 的倍数" % (i, indent))
        rows.append((indent, s.strip(), i))
    if not rows:
        return {}
    if rows[0][0] != 0:
        raise ValueError("第 %d 行：首行必须顶格" % rows[0][2])
    value, pos = _parse_block(rows, 0, 0)
    if pos != len(rows):
        raise ValueError("第 %d 行未被解析（缩进不一致？）" % rows[pos][2])
    return value


def load(path):
    """从文件路径读取并解析。"""
    with io.open(path, encoding="utf-8") as f:
        return loads(f.read())
