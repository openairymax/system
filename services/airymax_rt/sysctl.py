# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 内核 RT 带宽预算的读取
#
# 总预算与周期都是内核暴露的整数接口，但读取侧必须区分三种「拿不到」：
# 文件不存在（内核没编 RT 组调度）、内容为空（内核正忙或尚未初始化）、
# 内容不是整数（解析失败）。把三者都折成同一个 0 会让上层误判成「内核
# 禁止实时」，进而把 cgroup 写成 0，把本来能用的实时能力关掉。这里统一
# 折成 None，由报告层映射为独立判定 token。
#
# 周期单独校验：周期非正时任何预算都无意义，按不可读处理。

"""AirymaxOS RT bandwidth sysctl sources (X-01)."""

from __future__ import annotations

from dataclasses import dataclass

from .policy import Policy

__all__ = ["Budget", "budget", "read_value"]


@dataclass(frozen=True, slots=True)
class Budget:
    """内核对实时带宽的记账口径，单位为微秒。"""

    total: int
    period: int

    @property
    def limited(self) -> bool:
        """总预算为正且周期有效时才存在可切分的预算。"""
        return self.total > 0 and self.period > 0


def read_value(path: str) -> int | None:
    """读取一个整数接口；缺失、为空或非整数一律返回 None。"""
    try:
        with open(path, encoding="ascii", errors="replace") as handle:
            text = handle.read().strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        return int(text, 10)
    except ValueError:
        return None


def budget(policy: Policy) -> Budget | None:
    """读取策略指向的两个接口；任一项不可用即整体不可判定。"""
    total = read_value(policy.total_path)
    period = read_value(policy.period_path)
    if total is None or period is None or period <= 0:
        return None
    return Budget(total=total, period=period)
