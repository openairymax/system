# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 电源与温度策略的单点定义
#
# 285H 装机实测：platform_profile、EPP 与 scaling_governor 全部停在
# 上游与 BIOS 默认的 performance；热区 SEN1 的 critical trip 只有
# 80.05°C，40 分钟内 package_throttle_count 增长 16025 次。AirymaxOS
# 此前没有任何代码读过这些接口，也就没有一处策略把机器从「全速」拉回
# 可持续状态。本文件只描述「什么供电、什么温度应该是什么档位」，不碰
# 文件系统；探测、写入与判定各自成模块，验收侧可以在没有写权限的机器
# 上复算同一份结论。
#
# 设计依据：0.0.4 方案 X1 泳道，docs-linux/170-performance/
# 01-scheduling-performance.md §2.1 与 docs-linux/100-operations/
# 02-configuration.md §2.2。

"""AirymaxOS power and thermal policy (X-06)."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "COOL",
    "DEFAULT_CHOICES",
    "DEFAULT_CPUFREQ",
    "DEFAULT_PROFILE",
    "DEFAULT_SUPPLY",
    "DEFAULT_THERMAL",
    "DEFAULT_THROTTLE",
    "MARK",
    "NORMAL_AC",
    "NORMAL_BATTERY",
    "Policy",
    "Tuning",
]

DEFAULT_PROFILE = "/sys/firmware/acpi/platform_profile"

DEFAULT_CHOICES = "/sys/firmware/acpi/platform_profile_choices"

DEFAULT_CPUFREQ = "/sys/devices/system/cpu/cpufreq"

DEFAULT_SUPPLY = "/sys/class/power_supply"

DEFAULT_THERMAL = "/sys/class/thermal"

DEFAULT_THROTTLE = "/sys/devices/system/cpu/cpu0/thermal_throttle"

MARK = "AIRYMAX-POWER"


@dataclass(frozen=True, slots=True)
class Tuning:
    """一组要同时落地的电源档位。"""

    profile: str
    governor: str
    epp: str


NORMAL_AC = Tuning("balanced", "schedutil", "balance_power")

NORMAL_BATTERY = Tuning("low-power", "schedutil", "power")

COOL = Tuning("low-power", "powersave", "power")


@dataclass(frozen=True, slots=True)
class Policy:
    """一次判定或落地的输入；路径可替换，便于测试与救援环境。"""

    profile: str = DEFAULT_PROFILE
    choices: str = DEFAULT_CHOICES
    cpufreq: str = DEFAULT_CPUFREQ
    supply: str = DEFAULT_SUPPLY
    thermal: str = DEFAULT_THERMAL
    throttle: str = DEFAULT_THROTTLE
    ac: Tuning = NORMAL_AC
    battery: Tuning = NORMAL_BATTERY
    cool: Tuning = COOL
