# -*- coding: utf-8 -*-
"""三态布尔环境变量的**单一实现**（纯标准库）。

为什么单独一个模块
------------------
原先 `llm_answer.py` 与 `causal_graph.py` 各需要一份「0/1 门控」解析。
若各写一份，就会重演本项目已经踩过的坑：**同一判据的多份复制会静默分叉**
（`_edge_expandable` 就在 `causal_graph.py` 与 `llm_answer.py` 各有一份，红线 12 已登记）。
⇒ 这里只留**一份**实现，两个模块共用；`test_llm_answer_env.py` 另有一条断言
「两个模块解析到的是**同一个函数对象**」，防止将来又被复制。

语义（**三态**，不是两态）
--------------------------
| 取值 | 结果 |
|---|---|
| 未设置 / 空串 / 纯空白 | `default` |
| `0` / `false` / `no` / `off`（含大小写、含首尾空白） | `False` |
| 其余非空 | `True` |

⚠️ **不得**写成 `bool(os.environ.get(name))` —— 那会让 `NAME=0` 判为 **True**，
即「显式置 0 反而开启了该门控」。本项目实测同族共 **4 处**（`LLM_REV_EDGES` /
`LLM_PROMPT_CAUSE` / `LLM_FALLBACK` / `LLM_NEIGHBORS`）⇒ 凡 0/1 门控一律走本函数，
并在改动时做**三态自证**（未设置 / 空 / `=1` 行为须各异）。
"""

import os

_FALSY = ("0", "false", "no", "off")


def env_flag(name, default=False):
    """读三态布尔环境变量。"""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in _FALSY
