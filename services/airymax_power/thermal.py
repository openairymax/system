# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 热区采样与分级
#
# 285H 装机实测暴露了三件事，决定了这里的分级方式：其一，platform_profile
# 与 governor 全停在 performance，40 分钟内 package_throttle_count 增长
# 16025 次，机器始终在全速与过热的边界上；其二，各热区的 critical 门限
# 并不相同，SEN1 只有 80.05°C，用一个全机统一门限会把「离本区关机点多远」
# 抹平；其三，iwlwifi 热区会读出 -61°C 这类无效值。
#
# 因此分级按每个热区自己的 critical 门限算余量，而不是比绝对温度；读数
# 落在物理不可能区间时整条丢弃，不参与判定；mode 为 disabled 的热区只作为
# 证据展示，不驱动降档——内核不会按它关机，按它降档只会让机器长期停在
# 低功耗档。所有温度以毫摄氏度整数参与计算，显示时才折算，避免浮点误差
# 把 0.05°C 的余量算成跨级。

"""AirymaxOS thermal sampling (X-06)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path

from . import sysfs
from .policy import Policy

__all__ = [
    "CRITICAL_ABS",
    "CRITICAL_MARGIN",
    "HOT_ABS",
    "HOT_MARGIN",
    "Severity",
    "Thermal",
    "WARN_ABS",
    "WARN_MARGIN",
    "Zone",
    "collect",
    "grade",
    "worst",
]

CRITICAL_MARGIN = 3000

HOT_MARGIN = 8000

WARN_MARGIN = 15000

CRITICAL_ABS = 95000

HOT_ABS = 90000

WARN_ABS = 80000

FLOOR = -20000

CEIL = 150000

DISABLED = "disabled"


@unique
class Severity(Enum):
    UNKNOWN = "unknown"
    OK = "ok"
    WARN = "warn"
    HOT = "hot"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _ORDER.index(self)


_ORDER = (
    Severity.UNKNOWN,
    Severity.OK,
    Severity.WARN,
    Severity.HOT,
    Severity.CRITICAL,
)


@dataclass(frozen=True, slots=True)
class Zone:
    """一个热区的一次读数；温度单位为毫摄氏度。"""

    name: str
    temp: int
    critical: int | None
    mode: str | None

    @property
    def severity(self) -> Severity:
        return grade(self.temp, self.critical)

    @property
    def celsius(self) -> float:
        return round(self.temp / 1000, 1)

    @property
    def active(self) -> bool:
        return self.mode != DISABLED


@dataclass(frozen=True, slots=True)
class Thermal:
    """一次采样：全部热区读数与处理器节流计数。"""

    zones: tuple[Zone, ...]
    package: int | None
    core: int | None

    @property
    def severity(self) -> Severity:
        return worst(self.zones)

    @property
    def alarm(self) -> Zone | None:
        live = [zone for zone in self.zones if zone.active]
        if not live:
            return None
        return max(live, key=lambda zone: (zone.severity.rank, zone.temp))


def grade(temp: int | None, critical: int | None) -> Severity:
    """按本热区的 critical 门限分级；没有门限时退回绝对温标。"""
    if temp is None:
        return Severity.UNKNOWN
    if critical is None:
        return _abs(temp)
    margin = critical - temp
    if margin <= CRITICAL_MARGIN:
        return Severity.CRITICAL
    if margin <= HOT_MARGIN:
        return Severity.HOT
    if margin <= WARN_MARGIN:
        return Severity.WARN
    return Severity.OK


def worst(zones: tuple[Zone, ...]) -> Severity:
    """取全部有效热区中最严重的一档；没有有效热区时返回 UNKNOWN。"""
    live = [zone.severity for zone in zones if zone.active]
    if not live:
        return Severity.UNKNOWN
    return max(live, key=lambda item: item.rank)


def collect(policy: Policy) -> Thermal:
    """采样全部热区与 cpu0 的节流计数。"""
    zones = tuple(sorted(_zones(Path(policy.thermal)), key=_rank))
    throttle = Path(policy.throttle)
    return Thermal(
        zones,
        sysfs.read_int(throttle / "package_throttle_count"),
        sysfs.read_int(throttle / "core_throttle_count"),
    )


def _abs(temp: int) -> Severity:
    if temp >= CRITICAL_ABS:
        return Severity.CRITICAL
    if temp >= HOT_ABS:
        return Severity.HOT
    if temp >= WARN_ABS:
        return Severity.WARN
    return Severity.OK


def _rank(zone: Zone) -> tuple[int, int, str]:
    return (-zone.severity.rank, -zone.temp, zone.name)


def _zones(root: Path) -> list[Zone]:
    try:
        entries = sorted(root.glob("thermal_zone*"), key=sysfs.order)
    except OSError:
        return []
    zones = []
    for entry in entries:
        temp = sysfs.read_int(entry / "temp")
        if temp is None or not FLOOR <= temp <= CEIL:
            continue
        name = sysfs.read_text(entry / "type") or entry.name
        mode = sysfs.read_text(entry / "mode")
        zones.append(Zone(name, temp, _limit(entry), mode))
    return zones


def _limit(zone: Path) -> int | None:
    trips = []
    for path in zone.glob("trip_point_*_type"):
        if sysfs.read_text(path) != "critical":
            continue
        temp = sysfs.read_int(_twin(path))
        if temp is not None:
            trips.append(temp)
    return min(trips) if trips else None


def _twin(path: Path) -> Path:
    return path.with_name(path.name.removesuffix("_type") + "_temp")
