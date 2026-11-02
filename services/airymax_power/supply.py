# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 供电来源识别
#
# 同一台机器在 AC 与电池上的可持续档位不同：AC 可以交给固件在性能与风扇
# 曲线之间取舍，电池必须更早降档。识别只看 power_supply 类暴露的 type 与
# online/status，不读机型、不依赖 upower 之类的用户态守护进程——开机早期
# 那些服务还没起来，而这时正是最需要把固件从 performance 拉回来的时刻。
#
# 判据顺序：任一 Mains 在线即 AC；电池在充电或已充满也按 AC 处理，因为
# 此时负载由适配器承担；只有电池处于放电才是 BATTERY。有电池但状态读不到
# 时按电池处理，宁可降档也不把续航与发热赌在未知状态上。

"""AirymaxOS power source detection."""

from __future__ import annotations

from enum import Enum, unique
from pathlib import Path

from . import sysfs
from .policy import Policy

__all__ = ["BATTERY", "CHARGING", "DISCHARGING", "MAINS", "Source", "detect"]

MAINS = "Mains"

BATTERY = "Battery"

CHARGING = ("Charging", "Full")

DISCHARGING = "Discharging"


@unique
class Source(Enum):
    AC = "ac"
    BATTERY = "battery"
    UNKNOWN = "unknown"


def detect(policy: Policy) -> Source:
    """判定当前供电来源；没有 power_supply 的机器返回 UNKNOWN。"""
    try:
        entries = sorted(Path(policy.supply).iterdir())
    except OSError:
        return Source.UNKNOWN
    battery = False
    for entry in entries:
        kind = sysfs.read_text(entry / "type")
        if kind == MAINS and sysfs.read_text(entry / "online") == "1":
            return Source.AC
        if kind == BATTERY:
            battery = True
            if sysfs.read_text(entry / "status") in CHARGING:
                return Source.AC
            if sysfs.read_text(entry / "status") == DISCHARGING:
                return Source.BATTERY
    return Source.BATTERY if battery else Source.UNKNOWN
