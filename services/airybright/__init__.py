# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 背光管理包的公开接口
#
# 外部只需要四个动作：只读评估、写绝对值、相对调节与命令行入口。设备发现
# 与百分比换算留在各自模块，避免调用方绕过回读自己拼一套「写进去了」的
# 判定口径。

"""AirymaxOS backlight control."""

from .cli import main
from .device import Device, pick, primary, rank, scan
from .level import clamp, pct_of, raw_of, shift
from .plan import apply, read, step
from .policy import Policy, Target
from .report import Report, Row, State, missing, row_of

__all__ = [
    "Device",
    "Policy",
    "Report",
    "Row",
    "State",
    "Target",
    "apply",
    "clamp",
    "main",
    "missing",
    "pct_of",
    "pick",
    "primary",
    "rank",
    "raw_of",
    "read",
    "row_of",
    "scan",
    "shift",
    "step",
]
