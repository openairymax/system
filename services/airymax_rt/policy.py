# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# RT 带宽策略的单点定义
#
# 总预算取自内核的 kernel.sched_rt_runtime_us，按固定份数均分给一级
# slice，剩余份额留给未列出的组与内核自身。份数取 5 而不是「把总量写满
# 给某一处」：带宽是层级预算，子组之和不得超过父组，写满 system.slice
# 之后 user.slice 写任何值都返回 EINVAL（真机 B50 实测）。5 份里只列
# 4 个 slice，剩下的 1 份正是给这种层级约束留的余量。
#
# 设计依据：docs-linux/100-operations/02-configuration.md §2.2。

"""AirymaxOS RT bandwidth policy (X-01)."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "DEFAULT_CGROUP",
    "DEFAULT_DIVISOR",
    "DEFAULT_PERIOD",
    "DEFAULT_SLICES",
    "DEFAULT_TOTAL",
    "MARK",
    "Policy",
]

DEFAULT_TOTAL = "/proc/sys/kernel/sched_rt_runtime_us"

DEFAULT_PERIOD = "/proc/sys/kernel/sched_rt_period_us"

DEFAULT_CGROUP = "/sys/fs/cgroup"

DEFAULT_SLICES = (
    "system.slice",
    "user.slice",
    "airymax.slice",
    "airymax-desktop.slice",
)

DEFAULT_DIVISOR = 5

MARK = "AIRYMAX-RT-CAP"


@dataclass(frozen=True, slots=True)
class Policy:
    """一次带宽切分的输入；路径可替换，便于测试与救援环境。"""

    slices: tuple[str, ...] = DEFAULT_SLICES
    divisor: int = DEFAULT_DIVISOR
    total_path: str = DEFAULT_TOTAL
    period_path: str = DEFAULT_PERIOD
    cgroup_root: str = DEFAULT_CGROUP

    @property
    def wanted(self) -> int:
        """应有几个 slice 拿到预算；降级判定以它为分母。"""
        return len(self.slices)

    def share(self, total: int) -> int:
        """每份预算，向下取整；总预算非正或份数无效时为 0。"""
        if total <= 0 or self.divisor < 1:
            return 0
        return total // self.divisor
