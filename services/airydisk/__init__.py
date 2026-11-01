# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 磁盘管理服务包
#
# 设备模型（model）、探测层（probe）与操作层（ops）的命名空间入口。
# 对外只导出数据契约与护栏判定，外部命令的执行不在此包初始化即生效。

"""AirymaxOS disk management service package (M-30, M-31).

设计依据：docs-linux/190-distribution/03-installer-design.md §3。
"""

from __future__ import annotations

from .model import (
    SCHEMA_VERSION,
    SYSTEM_MOUNTS,
    Bus,
    Disk,
    FsKind,
    Partition,
    Role,
    TableKind,
    TopoKind,
    Topology,
)
from .ops import Action, Plan, Refusal, Step, refuse
from .runner import Outcome, Result, apply, run

__all__ = [
    "SCHEMA_VERSION",
    "SYSTEM_MOUNTS",
    "Action",
    "Bus",
    "Disk",
    "FsKind",
    "Outcome",
    "Partition",
    "Plan",
    "Refusal",
    "Result",
    "Role",
    "Step",
    "TableKind",
    "TopoKind",
    "Topology",
    "apply",
    "refuse",
    "run",
]
