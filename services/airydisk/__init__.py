# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 磁盘管理服务包
#
# 设备模型（model）、探测层（probe）与操作层（ops）的命名空间入口。
# 对外只导出数据契约，探测与操作由各自的实现段补入后再导出。

"""AirymaxOS disk management service package (M-30).

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

__all__ = [
    "SCHEMA_VERSION",
    "SYSTEM_MOUNTS",
    "Bus",
    "Disk",
    "FsKind",
    "Partition",
    "Role",
    "TableKind",
    "TopoKind",
    "Topology",
]
