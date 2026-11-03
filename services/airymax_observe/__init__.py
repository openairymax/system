# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 硬件清点
#
# 清点整机硬件：哪些设备被内核接住、哪些整类缺失、哪些存在但被开关关着。
# 只读 /sys，不加载模块、不改开关；判定与输出见 hw 与 cli。

"""AirymaxOS hardware observation (I-24)."""

from __future__ import annotations

from .hw import CLASSES, REQUIRED, Entry, Group, Survey, collect
from .cli import main

__all__ = [
    "CLASSES", "REQUIRED", "Entry", "Group", "Survey", "collect", "main",
]