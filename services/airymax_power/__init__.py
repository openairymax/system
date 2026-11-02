# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 电源与温度管理包的公开接口
#
# 外部只需要三件事：判定（guard.inspect/apply）、策略与档位类型、以及
# 命令行入口。sysfs 读写与采样细节留在各自模块，避免调用方绕过回读直接
# 写节点而制造第二套判定口径。

"""AirymaxOS power and thermal management."""

from .cli import main
from .duty import Duty, decide
from .guard import apply, assess, choose, inspect
from .policy import COOL, NORMAL_AC, NORMAL_BATTERY, Policy, Tuning
from .report import Report
from .supply import Source, detect
from .thermal import Severity, Thermal, Zone, collect, grade
from .tuning import Knob, State, verdict

__all__ = [
    "COOL",
    "Duty",
    "NORMAL_AC",
    "NORMAL_BATTERY",
    "Knob",
    "Policy",
    "Report",
    "Severity",
    "Source",
    "State",
    "Thermal",
    "Tuning",
    "Zone",
    "apply",
    "assess",
    "choose",
    "collect",
    "decide",
    "detect",
    "grade",
    "inspect",
    "main",
    "verdict",
]
