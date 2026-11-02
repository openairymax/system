# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 探针访问的内核接口位置
#
# 五个位置全部来自已合入的内核实现，不是约定占位：tracefs 两处挂载点、
# securityfs 的 policy 表、procfs 的 agentrt 目录与 cap_used、debugfs 的
# stc 统计。tracefs 优先取标准挂载点，缺失时回落到 debugfs 兼容挂载，
# 两者都不存在仍照常产出报告——由上层显式记为 unavailable，而不是让
# 探针自己退出。

"""AirymaxOS probe endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["Paths", "defaults"]

TRACE = "/sys/kernel/tracing"

LEGACY = "/sys/kernel/debug/tracing"


@dataclass(frozen=True, slots=True)
class Paths:
    """一次探测使用的接口根；测试可逐项替换。"""

    tracefs: str = TRACE
    policy: str = "/sys/kernel/security/airy/policy"
    agentrt: str = "/proc/sys/agentrt"
    cap: str = "/proc/sys/kernel/airy/cap_used"
    stc: str = "/sys/kernel/debug/airy_stc/stats"


def defaults() -> Paths:
    """按本机实际挂载点选择 tracefs 根。"""
    if Path(TRACE).is_dir():
        return Paths()
    if Path(LEGACY).is_dir():
        return Paths(tracefs=LEGACY)
    return Paths()