# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 背光报告的判定与序列化
#
# 三个状态必须有三个不同退出码（0.0.4 铁律③），因此这里把「做了」「部分
# 做了」「做不到」「没有设备」区分成四个状态，而不是把没有设备折叠进成功：
# KMS 还没接管时 `backlight` 目录是空的，若按成功返回，运维会以为亮度已经
# 调好，实际上桌面根本没起来。
#
# 只读与写入共用一套行结构：只读行 `want` 为空、`landed` 不看；写入行带
# 目标刻度并回读比对。缺席的一边也留一行（`present` 为假），否则「键盘
# 没找到」只能从缺少的那行推断出来。

"""AirymaxOS backlight report."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

from . import level
from .device import Device
from .policy import MARK

__all__ = ["Report", "Row", "State", "missing", "row_of"]


@unique
class State(Enum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    ABSENT = "absent"


@dataclass(frozen=True, slots=True)
class Row:
    """一台设备的一行结果；缺席设备用 present=False 留位。"""

    kind: str
    name: str
    type: str
    maximum: int
    raw: int | None
    want: int | None
    pct: int | None
    present: bool
    landed: bool
    error: str | None = None

    @classmethod
    def of(cls, dev: Device, *, raw: int | None = None,
           want: int | None = None, pct: int | None = None,
           landed: bool = False, error: str | None = None) -> Row:
        """按设备身份铺一行；只读与写入共用同一组身份字段。"""
        return cls(kind=dev.kind, name=dev.name, type=dev.type,
                   maximum=dev.maximum, raw=raw, want=want, pct=pct,
                   present=True, landed=landed, error=error)

    def dump(self) -> dict[str, object]:
        return {
            "error": self.error,
            "kind": self.kind,
            "landed": self.landed,
            "max": self.maximum,
            "name": self.name,
            "pct": self.pct,
            "present": self.present,
            "raw": self.raw,
            "type": self.type,
            "want": self.want,
        }


@dataclass(frozen=True, slots=True)
class Report:
    mode: str
    target: str
    floor: int
    rows: tuple[Row, ...]

    @property
    def present(self) -> int:
        return sum(1 for row in self.rows if row.present)

    @property
    def landed(self) -> int:
        return sum(1 for row in self.rows if row.landed)

    @property
    def state(self) -> State:
        live = [row for row in self.rows if row.present]
        if not live:
            return State.ABSENT
        if self.mode == "read":
            if any(row.pct is not None for row in live):
                return State.OK
            return State.FAILED
        good = [row for row in live if row.landed]
        if len(good) == len(self.rows):
            return State.OK
        return State.PARTIAL if good else State.FAILED

    def dump(self) -> dict[str, object]:
        return {
            "devices": [row.dump() for row in self.rows],
            "floor": self.floor,
            "landed": self.landed,
            "mark": MARK,
            "mode": self.mode,
            "present": self.present,
            "state": self.state.value,
            "target": self.target,
        }


def row_of(dev: Device) -> Row:
    """只读一行：保留内核回读刻度，百分比现算。"""
    raw = dev.level
    pct = None if raw is None else level.pct_of(raw, dev.maximum)
    return Row.of(dev, raw=raw, pct=pct)


def missing(kind: str) -> Row:
    """目标里缺席的一边；error 固定为 no-device，便于脚本按字段分流。"""
    return Row(kind=kind, name="-", type="", maximum=0, raw=None, want=None,
               pct=None, present=False, landed=False, error="no-device")
