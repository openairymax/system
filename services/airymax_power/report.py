# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 电源与温度报告
#
# 一次评估的结论由五块证据组成：供电来源、电池电量、热状态、节流增速、
# 控制点落地情况。五者各自有独立结论，不能压缩成单一布尔值——「档位写不
# 进去」和「写进去了但机器已经很热」是两类不同的故障，现场处置完全不同；
# 电量解释了「为什么在电池上还选了最省电档」；节流增速则补上温度看不到的
# 功耗墙限速。整体严重度取温度与节流增速中更严重的一档。报告只负责把这些
# 证据原样收敛成一份可序列化的结构，判定与退出码留在调用方。

"""AirymaxOS power report."""

from __future__ import annotations

from dataclasses import dataclass

from . import throttle
from .duty import Duty
from .supply import Source
from .thermal import Severity, Thermal, Zone, peak
from .tuning import Knob, State

__all__ = ["Report"]


@dataclass(frozen=True, slots=True)
class Report:
    state: State
    source: Source
    charge: int | None
    heat: Thermal
    knobs: tuple[Knob, ...]
    rate: float | None = None
    duty: Duty = Duty.NONE

    @property
    def present(self) -> int:
        return sum(1 for knob in self.knobs if knob.present)

    @property
    def landed(self) -> int:
        return sum(1 for knob in self.knobs if knob.landed)

    @property
    def severity(self) -> Severity:
        return peak(self.heat.severity, throttle.grade(self.rate))

    def dump(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "source": self.source.value,
            "charge": self.charge,
            "duty": self.duty.value,
            "severity": self.severity.value,
            "present": self.present,
            "landed": self.landed,
            "package_throttle": self.heat.package,
            "core_throttle": self.heat.core,
            "throttle_rate": _round(self.rate),
            "zones": [_zone(zone) for zone in self.heat.zones],
            "knobs": [_knob(knob) for knob in self.knobs],
        }


def _round(rate: float | None) -> float | None:
    return None if rate is None else round(rate, 3)


def _zone(zone: Zone) -> dict[str, object]:
    return {
        "name": zone.name,
        "temp": zone.celsius,
        "critical": None if zone.critical is None else zone.critical / 1000,
        "mode": zone.mode,
        "severity": zone.severity.value,
    }


def _knob(knob: Knob) -> dict[str, object]:
    return {
        "name": knob.name,
        "target": knob.target,
        "current": knob.current,
        "present": knob.present,
        "landed": knob.landed,
    }
