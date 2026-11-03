# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 整机硬件清点
#
# 0.0.4 要在 285H 上回答一个具体问题：烧好的机器上，到底哪些硬件被内核
# 接住了、哪些根本没出现、哪些出现了但被关着。这三件事必须分开说，否则
# 「Wi-Fi 连不上」会被误判成网卡没驱动，「蓝牙不能用」会被误判成没有蓝牙。
#
# 因此清点只认 sysfs 的节点，不解析 dmesg、不猜型号：
#
#   - absent：整类一个设备都没有，说明驱动或固件没接上；
#   - 状态：原样取自 sysfs（online / operstate / temp / status / state）；
#   - blocked：设备存在但被 rfkill 软/硬开关关着，这是唯一可以「启用」的
#     状态，也是本模块唯一会判成 partial 的情形。
#
# 必检项只有三项：cpu、block、net。没有 CPU 或根盘就没有系统；没有网络
# 就无法联机验收。其余类别（显卡、声卡、USB、TPM、看门狗、电源、温度、
# 散热、rfkill）在 ARM 与 RISC-V 开发板上未必齐全，缺了只标缺失，不能让
# 门禁在合法平台上误报。
#
# 节点名与设备标签分开：name 是 sysfs 目录名（cpu0、wlan0、rfkill1），
# label 是设备自报的类型或型号（wlan、PCH、BAT1）。两者都进 JSON，人读
# 输出优先用 name，因为它是唯一不会重名的标识——285H 上就有两个 bluetooth
# 节点，只报类型会分不清是哪一个被关着。
#
# 排序复用 power 侧的 sysfs.order：cpu2 排在 cpu10 之前，wlan0 排在
# wlan10 之前，报告顺序与硬件编号一致。

"""AirymaxOS hardware survey (I-24)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from airymax_power import sysfs

__all__ = ["BLOCKED", "CLASSES", "Entry", "Group", "Survey", "collect"]

BLOCKED = frozenset({"blocked", "off", "disabled", "offline"})

REQUIRED = frozenset({"cpu", "block", "net"})

_BY_NAME = {"cpu": "cpu", "gpu": "card"}


@dataclass(frozen=True, slots=True)
class Spec:
    """一个设备类的读法：去哪里找、状态读哪个文件、标签读哪个文件。"""

    name: str
    glob: str
    state: tuple[str, ...] = ()
    label: str = ""


CLASSES = (
    Spec("cpu", "devices/system/cpu/cpu*", ("online",)),
    Spec("block", "class/block/*"),
    Spec("net", "class/net/*", ("operstate",)),
    Spec("rfkill", "class/rfkill/*", label="type"),
    Spec("gpu", "class/drm/card*"),
    Spec("sound", "class/sound/card[0-9]*", label="id"),
    Spec("usb", "bus/usb/devices/usb*", label="product"),
    Spec("thermal", "class/thermal/thermal_zone*", ("temp",), "type"),
    Spec("hwmon", "class/hwmon/hwmon*", label="name"),
    Spec("power", "class/power_supply/*", ("status",), "type"),
    Spec("tpm", "class/tpm/tpm*"),
    Spec("watchdog", "class/watchdog/watchdog*", ("state",)),
)


@dataclass(frozen=True, slots=True)
class Entry:
    name: str
    state: str
    label: str = ""
    driver: str = ""

    @property
    def blocked(self) -> bool:
        return self.state in BLOCKED

    def dump(self) -> dict[str, str]:
        return {
            "name": self.name,
            "label": self.label,
            "state": self.state,
            "driver": self.driver,
        }


@dataclass(frozen=True, slots=True)
class Group:
    name: str
    entries: tuple[Entry, ...]

    @property
    def required(self) -> bool:
        return self.name in REQUIRED

    @property
    def absent(self) -> bool:
        return not self.entries

    @property
    def blocked(self) -> int:
        return sum(1 for entry in self.entries if entry.blocked)

    def dump(self) -> dict[str, object]:
        return {
            "required": self.required,
            "count": len(self.entries),
            "blocked": self.blocked,
            "devices": [entry.dump() for entry in self.entries],
        }


@dataclass(frozen=True, slots=True)
class Survey:
    groups: tuple[Group, ...]

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(group.name for group in self.groups
                     if group.required and group.absent)

    @property
    def total(self) -> int:
        return sum(len(group.entries) for group in self.groups)

    @property
    def blocked(self) -> tuple[str, ...]:
        return tuple(_blocked(group, entry) for group in self.groups
                     for entry in group.entries if entry.blocked)

    def dump(self) -> dict[str, object]:
        return {
            "groups": {group.name: group.dump() for group in self.groups},
            "total": self.total,
            "missing": list(self.missing),
            "blocked": list(self.blocked),
        }


def collect(root: Path | str = "/sys") -> Survey:
    """按 CLASSES 逐类清点；只读 sysfs，不加载模块也不改开关。"""
    base = Path(root)
    return Survey(tuple(_group(spec, base) for spec in CLASSES))


def _group(spec: Spec, base: Path) -> Group:
    try:
        paths = sorted(base.glob(spec.glob), key=sysfs.order)
    except OSError:
        return Group(spec.name, ())
    return Group(spec.name, tuple(
        _entry(spec, path) for path in paths if _keeper(spec, path)))


def _keeper(spec: Spec, path: Path) -> bool:
    """cpu* 混着 cpufreq，card* 混着 card0-HDMI，只留编号节点。"""
    stem = _BY_NAME.get(spec.name)
    return stem is None or path.name[len(stem):].isdigit()


def _entry(spec: Spec, path: Path) -> Entry:
    if spec.name == "rfkill":
        return _radio(path)
    return Entry(path.name, _state(spec, path),
                 sysfs.read_text(path / spec.label) if spec.label else "",
                 _driver(path))


def _state(spec: Spec, path: Path) -> str:
    for candidate in spec.state:
        text = sysfs.read_text(path / candidate)
        if not text:
            continue
        if spec.name == "cpu":
            return "offline" if text == "0" else "online"
        return text
    return "online" if spec.name == "cpu" else "present"


def _radio(path: Path) -> Entry:
    """rfkill 的关断是 soft/hard 两个位，任一为 1 即不可用。"""
    label = sysfs.read_text(path / "type")
    off = (sysfs.read_text(path / "soft") == "1"
           or sysfs.read_text(path / "hard") == "1")
    return Entry(path.name, "blocked" if off else "ready", label)


def _blocked(group: Group, entry: Entry) -> str:
    mark = f"({entry.label})" if entry.label else ""
    return f"{group.name}/{entry.name}{mark}"


def _driver(path: Path) -> str:
    """驱动名取链接目标的名字；不存在的候选不能当作驱动。"""
    for candidate in (path / "driver", path / "device" / "driver"):
        if candidate.exists() or candidate.is_symlink():
            return candidate.resolve().name
    return ""