# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 实时调度带宽服务包
#
# 内核启用 CONFIG_RT_GROUP_SCHED 后，实时带宽按 cgroup 层级记账，新建组
# 的 cpu.rt_runtime_us 默认是 0。0 不是「不限」而是「禁止」：组内任何
# sched_setscheduler(SCHED_RR/SCHED_FIFO) 都返回 EPERM，rtkit-daemon 于
# 是每次启动都退化为非实时，音频与桌面交互在高负载下爆音卡顿，而所有
# 上层配置看上去都对。本包把「总预算从哪读、分给谁、怎么算生效」收在
# 一处，命令行、启动单元与验收门禁共用同一份实现。
#
# 设计依据：docs-linux/100-operations/02-configuration.md §2.2 与 §3，
# docs-linux/170-performance/01-scheduling-performance.md §2.1，
# docs-linux/170-performance/07-rt-matrix.md。

"""AirymaxOS RT service package (X-01, X-03)."""

from __future__ import annotations

from . import memcap
from .allocation import Entry, allocate
from .bandwidth import apply, inspect
from .cgroup import Kind, Layout, detect
from .defaults import Defaults, Seed, seed, seed_subtree
from .matrix import Cap, Flag, Grade, Matrix, Roots, collect
from .policy import (
    DEFAULT_CGROUP,
    DEFAULT_DIVISOR,
    DEFAULT_PERIOD,
    DEFAULT_SLICES,
    DEFAULT_TOTAL,
    MARK,
    Policy,
)
from .report import Landing, Report, State
from .sysctl import Budget, budget, read_value

__all__ = [
    "DEFAULT_CGROUP",
    "DEFAULT_DIVISOR",
    "DEFAULT_PERIOD",
    "DEFAULT_SLICES",
    "DEFAULT_TOTAL",
    "MARK",
    "Budget",
    "Cap",
    "Defaults",
    "Entry",
    "Flag",
    "Grade",
    "Kind",
    "Landing",
    "Layout",
    "Matrix",
    "Policy",
    "Report",
    "Roots",
    "Seed",
    "State",
    "allocate",
    "apply",
    "budget",
    "collect",
    "detect",
    "inspect",
    "read_value",
    "seed",
    "seed_subtree",
]
