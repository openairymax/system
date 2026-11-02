# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 加速探测的文件系统读取
#
# 探测跑在装机后的服务身份下，容器里可能什么都挂不进来，目录也可能存在
# 却不可读。列出目录失败不该让整个探测崩掉——那样一个不可读的 /dev/dri
# 就会把服务拦在启动之前，而正确的结果只是「这一路不可用」。

"""Filesystem listing shared by the accelerator checks (K-22-01)."""

from __future__ import annotations

from pathlib import Path

__all__ = ["names"]


def names(directory: Path, pattern: str) -> tuple[str, ...]:
    """列出目录内匹配的文件名并按字典序排列。

    Args:
        directory: 目标目录。
        pattern: glob 模式，如 ``renderD*``。

    Returns:
        文件名元组；目录不存在或不可读时为空元组。
    """
    try:
        return tuple(sorted(item.name for item in directory.glob(pattern)))
    except OSError:
        return ()
