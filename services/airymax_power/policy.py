# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 电源与温度策略的单点定义
#
# 285H 装机实测：platform_profile、EPP 与 scaling_governor 全部停在
# 上游与 BIOS 默认的 performance；热区 SEN1 的 critical trip 只有
# 80.05°C，40 分钟内 package_throttle_count 增长 16025 次。AirymaxOS
# 此前没有任何代码读过这些接口，也就没有一处策略把机器从「全速」拉回
# 可持续状态。本文件只描述「什么供电、什么温度、什么负载应该是什么档位」，
# 不碰文件系统；探测、写入与判定各自成模块，验收侧可以在没有写权限的机器
# 上复算同一份结论。

"""AirymaxOS power and thermal policy."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "BUSY",
    "COOL",
    "DEFAULT_BEAT",
    "DEFAULT_CEIL",
    "DEFAULT_CHOICES",
    "DEFAULT_CPUFREQ",
    "DEFAULT_CPUS",
    "DEFAULT_DUTY",
    "DEFAULT_LOADAVG",
    "DEFAULT_MEMINFO",
    "DEFAULT_PAUSE",
    "DEFAULT_POWEROFF",
    "DEFAULT_PRESS",
    "DEFAULT_PROFILE",
    "DEFAULT_PSI",
    "DEFAULT_PSTATE",
    "DEFAULT_SUPPLY",
    "DEFAULT_THERMAL",
    "DEFAULT_STATE",
    "DEFAULT_THROTTLE",
    "DEFAULT_WALL",
    "MARK",
    "NORMAL_AC",
    "NORMAL_BATTERY",
    "Policy",
    "Tuning",
]

DEFAULT_PROFILE = "/sys/firmware/acpi/platform_profile"

DEFAULT_CHOICES = "/sys/firmware/acpi/platform_profile_choices"

DEFAULT_CPUFREQ = "/sys/devices/system/cpu/cpufreq"

DEFAULT_CPUS = "/sys/devices/system/cpu"

DEFAULT_PSTATE = "/sys/devices/system/cpu/intel_pstate"

DEFAULT_SUPPLY = "/sys/class/power_supply"

DEFAULT_THERMAL = "/sys/class/thermal"

DEFAULT_THROTTLE = "/sys/devices/system/cpu/cpu0/thermal_throttle"

DEFAULT_PSI = "/proc/pressure"

DEFAULT_LOADAVG = "/proc/loadavg"

DEFAULT_MEMINFO = "/proc/meminfo"

DEFAULT_STATE = "/run/airy-power/throttle"

DEFAULT_CEIL = "/run/airy-power/ceiling"

DEFAULT_PRESS = "/run/airy-power/pressure"

DEFAULT_DUTY = "/run/airy-power/duty"

DEFAULT_BEAT = "/run/airy-power/beat"

DEFAULT_PAUSE = "/run/airy-power/pause"

DEFAULT_POWEROFF = "/usr/bin/systemctl"

DEFAULT_WALL = "/usr/bin/wall"

MARK = "AIRYMAX-POWER"


@dataclass(frozen=True, slots=True)
class Tuning:
    """一组要同时落地的电源档位。

    profile 是固件档位；governors 与 epps 是按优先级排列的候选值，由
    tuning 在每次评估时对照内核给出的可选集合取第一个命中项。候选不能
    写成单一值：intel_pstate 的 active 模式只提供 performance 与
    powersave，把 governor 钉在 schedutil 会让该控制点在所有现代 Intel
    机器上永远不适用，判定也就永远无法收敛到 ok。
    """

    profile: str
    governors: tuple[str, ...]
    epps: tuple[str, ...]


NORMAL_AC = Tuning("balanced", ("schedutil", "powersave"), ("balance_power",))

NORMAL_BATTERY = Tuning("low-power", ("schedutil", "powersave"),
                        ("balance_power",))

BUSY = Tuning("balanced", ("schedutil", "powersave"),
              ("balance_performance", "balance_power"))

COOL = Tuning("low-power", ("powersave", "schedutil"), ("power",))


@dataclass(frozen=True, slots=True)
class Policy:
    """一次判定或落地的输入；路径可替换，便于测试与救援环境。"""

    profile: str = DEFAULT_PROFILE
    choices: str = DEFAULT_CHOICES
    cpufreq: str = DEFAULT_CPUFREQ
    cpus: str = DEFAULT_CPUS
    pstate: str = DEFAULT_PSTATE
    supply: str = DEFAULT_SUPPLY
    thermal: str = DEFAULT_THERMAL
    throttle: str = DEFAULT_THROTTLE
    psi: str = DEFAULT_PSI
    loadavg: str = DEFAULT_LOADAVG
    meminfo: str = DEFAULT_MEMINFO
    state: str = DEFAULT_STATE
    ceil: str = DEFAULT_CEIL
    press: str = DEFAULT_PRESS
    duty: str = DEFAULT_DUTY
    beat: str = DEFAULT_BEAT
    pause: str = DEFAULT_PAUSE
    poweroff: str = DEFAULT_POWEROFF
    wall: str = DEFAULT_WALL
    ac: Tuning = NORMAL_AC
    busy: Tuning = BUSY
    battery: Tuning = NORMAL_BATTERY
    cool: Tuning = COOL
