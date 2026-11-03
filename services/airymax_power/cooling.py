# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 散热设备的清单与能力判定
#
# 285H 装机实测：四个 Fan 类 cooling_device 是 ACPI PNP0C0B 风扇对象，固件
# 没有实现 \_TZ.FNCL。向 cur_state 写入返回 ENODEV，dmesg 留下
# "ACPI Error: Aborting method \_TZ.FNCL due to previous error (AE_NOT_FOUND)"，
# 此后该节点的 cur_state 连读都失败。也就是说这台机器的风扇由 EC 与固件
# 独占，内核侧没有可用的执行面；任何「写风扇降温」的实现只会把 ACPI 方法
# 打坏一次，风扇转速一点不变。
#
# 所以本模块只做清单：机上有哪些散热设备、属于哪一类、当前在第几档、能不能
# 读回。它不写任何节点，也不给「可写」这种没有证据的结论——能不能写要写一次
# 才知道，而这一次在 285H 上已经证明是有害的。散热闭环因此落在
# platform_profile、governor/EPP 与空闲深度三处（见 tuning 与 idle），这里
# 只负责把「风扇不归内核管」变成可复现的证据，而不是让它从报告里消失。
#
# 分类不按名字猜：type 是内核给出的唯一权威标签，Fan 与 Processor 的执行面
# 完全不同——前者是风扇，后者是 cpufreq 冷却（降频），混为一谈会让运维以为
# 机器有风扇控制。读不到 cur_state 的设备照样列出，标记为不可读，因为
# 「设备存在但读不出档位」本身就是 0.0.4 要暴露的硬件事实。

"""AirymaxOS cooling inventory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import sysfs
from .policy import Policy

__all__ = ["CLAMP", "CORE", "FAN", "OTHER", "Cooling", "Device", "collect"]

FAN = "fan"

CORE = "cpu"

CLAMP = "clamp"

OTHER = "other"

_STEMS = (
    ("fan", FAN),
    ("processor", CORE),
    ("cpu", CORE),
    ("clamp", CLAMP),
)


@dataclass(frozen=True, slots=True)
class Device:
    """一个散热设备的一次读回；档位读不出来时为 None。"""

    name: str
    label: str
    kind: str
    limit: int
    state: int | None

    @property
    def readable(self) -> bool:
        return self.state is not None

    @property
    def engaged(self) -> bool:
        return bool(self.state)


@dataclass(frozen=True, slots=True)
class Cooling:
    """一次采样的全部散热设备。"""

    devices: tuple[Device, ...]

    def count(self, kind: str) -> int:
        return sum(1 for device in self.devices if device.kind == kind)

    @property
    def fans(self) -> tuple[Device, ...]:
        return tuple(device for device in self.devices if device.kind == FAN)

    @property
    def readable(self) -> int:
        return sum(1 for device in self.devices if device.readable)

    @property
    def engaged(self) -> int:
        return sum(1 for device in self.devices if device.engaged)

    def dump(self) -> dict[str, object]:
        return {
            "total": len(self.devices),
            "fans": self.count(FAN),
            "readable": self.readable,
            "engaged": self.engaged,
            "devices": [_device(device) for device in self.devices],
        }


def collect(policy: Policy) -> Cooling:
    """列出全部散热设备；只读，不触碰 cur_state 的写端。"""
    try:
        entries = sorted(Path(policy.thermal).glob("cooling_device*"),
                         key=sysfs.order)
    except OSError:
        return Cooling(())
    return Cooling(tuple(_device_at(entry) for entry in entries))


def _device_at(entry: Path) -> Device:
    label = sysfs.read_text(entry / "type") or entry.name
    return Device(entry.name, label, _kind(label),
                  sysfs.read_int(entry / "max_state") or 0,
                  sysfs.read_int(entry / "cur_state"))


def _kind(label: str) -> str:
    lowered = label.lower()
    for stem, kind in _STEMS:
        if stem in lowered:
            return kind
    return OTHER


def _device(device: Device) -> dict[str, object]:
    return {
        "name": device.name,
        "type": device.label,
        "kind": device.kind,
        "max": device.limit,
        "state": device.state,
    }