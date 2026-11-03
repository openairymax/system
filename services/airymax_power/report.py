# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 电源与温度报告
#
# 一次评估的结论由八块证据组成：供电来源、电池电量、热状态、节流增速、
# 整机负载、散热设备、停顿留痕、控制点落地情况。八者各自有独立结论，不能
# 压缩成单一布尔值——「档位写不进去」与「写进去了但机器已经很热」是两类完全
# 不同的故障，现场处置手段也不同；电量解释了「为什么在电池上还选了最省电档」；
# 节流增速则补上温度看不到的功耗墙限速；负载解释了「温度不高为什么还要加档」
# ——285H 的卡死发生在内存回收与 CPU 排队上，温度计上完全看不出来。停顿是负载
# 而不是原因：整机被挡住时连日志都写不出来，只有周期任务迟到的时长能留下
# 证据。整体严重度取温度与节流增速中更严重的一档，负载单独成项：一台满载
# 的机器不是故障，一台过热的机器才是。报告只负责把这些证据原样收敛成一份
# 可序列化的结构，判定与退出码留在调用方。

"""AirymaxOS power report."""

from __future__ import annotations

from dataclasses import dataclass

from . import throttle
from .cooling import Cooling
from .duty import Duty
from .load import Load
from .stall import Pause
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
    load: Load
    cool: Cooling
    pause: Pause | None = None
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
            "load": self.load.dump(),
            "cooling": self.cool.dump(),
            "pause": None if self.pause is None else self.pause.dump(),
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
