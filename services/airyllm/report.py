# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 模型运行状态的对外表示
#
# 状态有两种读者：人看一行，脚本读 token。两者都从同一个状态对象派生，
# 不各自拼字符串——否则 CLI、自检与 systemd 会给出三套措辞，排障时先
# 要判断三处差异是不是同一件事。
#
# token 只由状态与原因组成，不带版本与摘要：它要能被 grep、被 systemd
# 条件表达式直接引用；富字段留在行式输出里。

"""AirymaxOS model runtime status rendering (K-20-08).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
（degraded:<原因> 是稳定的对外 token）。

"""

from __future__ import annotations

from . import model

__all__ = ["line", "token"]


def token(entry: model.Status) -> str:
    """生成可被脚本匹配的状态 token。

    Args:
        entry: 状态对象。

    Returns:
        ``ready``、``degraded:<原因>`` 或 ``failed:<原因>``。
    """
    if entry.reason is None:
        return entry.state.value
    return entry.state.value + model.REASON_SEP + entry.reason.value


def line(entry: model.Status) -> str:
    """生成单行人类可读摘要。

    Args:
        entry: 状态对象。

    Returns:
        以空格分隔的 ``key=value`` 序列；无原因与无摘要时省略对应字段。
    """
    fields = [f"state={entry.state.value}",
              f"backend={entry.backend.value}"]
    if entry.reason is not None:
        fields.append(f"reason={entry.reason.value}")
    if entry.digest:
        fields.append(f"digest={entry.digest}")
    return " ".join(fields)
