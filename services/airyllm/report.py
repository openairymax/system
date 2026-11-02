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
#
# 行式与 JSON 两种渲染共用一份字段映射：字段名一旦分头维护，CLI 与日志
# 迟早会给出两套键名，排障时先要确认两处说的是不是同一件事。
#
# 探测结论也由本模块渲染：它和状态一样同时面向人和脚本，且依据要能被
# 原样打印。两块渲染放在一起，是为了让「一条后端一行」的措辞只有一份。

"""AirymaxOS model runtime status rendering (K-20-08).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
（degraded:<原因> 是稳定的对外 token）。

"""

from __future__ import annotations

from collections.abc import Sequence

from . import model
from .accel import Observation

__all__ = ["fields", "line", "probe_fields", "probe_line", "token"]


def fields(entry: model.Status) -> dict[str, str | None]:
    """把状态摊成对外字段映射。

    Args:
        entry: 状态对象。

    Returns:
        按 ``state``、``backend``、``reason``、``digest`` 顺序排列的映射；
        无原因时 ``reason`` 为 ``None``，无摘要时 ``digest`` 为空串。
    """
    return {
        "state": entry.state.value,
        "backend": entry.backend.value,
        "reason": None if entry.reason is None else entry.reason.value,
        "digest": entry.digest,
    }


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
    pairs = (f"{key}={value}" for key, value in fields(entry).items()
             if value not in (None, ""))
    return " ".join(pairs)


def probe_fields(rows: Sequence[Observation]) -> dict[str, object]:
    """把探测观察摊成对外字段映射。

    Args:
        rows: 探测观察，顺序即报告顺序。

    Returns:
        ``{"backends": [...]}``；每项含 ``backend``、``present`` 与
        ``evidence``，字段顺序固定。
    """
    return {"backends": [_backend_row(row) for row in rows]}


def probe_line(rows: Sequence[Observation]) -> str:
    """生成单行探测摘要。

    Args:
        rows: 探测观察，顺序即报告顺序。

    Returns:
        以空格分隔的 ``后端=present|absent`` 序列。
    """
    return " ".join(f"{row.backend.value}={_flag(row.present)}"
                    for row in rows)


def _backend_row(row: Observation) -> dict[str, object]:
    return {
        "backend": row.backend.value,
        "present": row.present,
        "evidence": list(row.evidence),
    }


def _flag(present: bool) -> str:
    return "present" if present else "absent"
