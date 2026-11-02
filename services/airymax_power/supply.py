# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 供电来源识别
#
# 同一台机器在 AC 与电池上的可持续档位不同：AC 可以交给固件在性能与风扇
# 曲线之间取舍，电池必须更早降档。识别只看 power_supply 类暴露的 type 与
# online/status，不读机型、不依赖 upower 之类的用户态守护进程——开机早期
# 那些服务还没起来，而这时正是最需要把固件从 performance 拉回来的时刻。
#
# 判据顺序：Mains 在线且电池同时放电，说明适配器功率不足以覆盖当前负载，
# 记为 WEAK——此时仍按 AC 处理会让系统继续维持高功耗，电量在「已插电」的
# 假象下持续流失；Mains 在线且电池不在放电才是 AC；电池在充电或已充满也
# 按 AC 处理，因为此时负载由适配器承担；只有电池处于放电才是 BATTERY。
# 有电池但状态读不到时按电池处理，宁可降档也不把续航与发热赌在未知状态上。
#
# 电量单独读出，不参与供电来源判定：285H 现场取证时适配器未在线、电池
# 30% 且放电中，机器仍在跑满核负载，几十瓦的持续放电会把电量耗尽并以
# 掉电关机收场——现场表现为「意外重启」，系统侧没有任何关机记录。电量
# 降到 LOW_CHARGE 以下时按最省电档位处理，并在报告里带上读数，运维不必
# 登录就能看出这次降档是因为热还是因为快没电了。

"""AirymaxOS power source detection."""

from __future__ import annotations

from enum import Enum, unique
from pathlib import Path

from . import sysfs
from .policy import Policy

__all__ = [
    "BATTERY",
    "CHARGING",
    "DISCHARGING",
    "LOW_CHARGE",
    "MAINS",
    "Source",
    "charge",
    "detect",
]

MAINS = "Mains"

BATTERY = "Battery"

CHARGING = ("Charging", "Full")

DISCHARGING = "Discharging"

LOW_CHARGE = 10


@unique
class Source(Enum):
    AC = "ac"
    BATTERY = "battery"
    WEAK = "weak"
    UNKNOWN = "unknown"


def detect(policy: Policy) -> Source:
    """判定当前供电来源；没有 power_supply 的机器返回 UNKNOWN。"""
    mains, battery, charging, draining = _scan(policy)
    if mains and draining:
        return Source.WEAK
    if mains or charging:
        return Source.AC
    return Source.BATTERY if battery else Source.UNKNOWN


def _scan(policy: Policy) -> tuple[bool, bool, bool, bool]:
    """一次遍历收集四类事实，避免边扫边返回漏掉后出现的电池。"""
    mains = battery = charging = draining = False
    for entry in _entries(policy):
        kind = sysfs.read_text(entry / "type")
        if kind == MAINS:
            mains = mains or sysfs.read_text(entry / "online") == "1"
        elif kind == BATTERY:
            battery = True
            status = sysfs.read_text(entry / "status")
            charging = charging or status in CHARGING
            draining = draining or status == DISCHARGING
    return mains, battery, charging, draining


def charge(policy: Policy) -> int | None:
    """取最低的一块电池电量百分比；没有可读电池时返回 None。"""
    levels = []
    for entry in _entries(policy):
        if sysfs.read_text(entry / "type") != BATTERY:
            continue
        level = sysfs.read_int(entry / "capacity")
        if level is not None and 0 <= level <= 100:
            levels.append(level)
    return min(levels) if levels else None


def _entries(policy: Policy) -> list[Path]:
    try:
        return sorted(Path(policy.supply).iterdir())
    except OSError:
        return []
