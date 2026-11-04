# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 背光设备发现与主设备选择
#
# 只读 sysfs，不做任何写入；写入与回读在 plan 里，判定在 report 里。发现
# 阶段只承认同时具备 `max_brightness` 且大于 0 的节点——没有上限就无从把
# 百分比换算成刻度，把它当成设备会让后续每一步都退化成猜测。
#
# 键盘背光在 led 类里没有统一命名：常见 `platform::kbd_backlight`、
# `tpacpi::kbd_backlight`，少数机型直接叫 `kbd_backlight`。因此按子串
# `kbd_backlight` 匹配，而不是并列枚举前缀；`input0::capslock` 之类不含该
# 子串的 led 一律不参与。
#
# 主设备只代表「默认写哪一条」，其余候选项仍保留在扫描结果里，报告可以
# 逐台列出，避免多面板机器上「调了不亮的那块」无从解释。

"""AirymaxOS backlight device discovery."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .policy import KBD_SUFFIX, TYPE_ORDER, Policy, Target

__all__ = [
    "KBD",
    "LEDS",
    "SCREEN",
    "Device",
    "pick",
    "primary",
    "rank",
    "read_int",
    "read_text",
    "scan",
]

SCREEN = "screen"

KBD = "keyboard"

LEDS = "leds"


@dataclass(frozen=True, slots=True)
class Device:
    """一台可调背光设备的静态事实与最近一次读数。"""

    name: str
    kind: str
    type: str
    maximum: int
    current: int | None
    actual: int | None
    path: Path
    writable: bool

    @property
    def node(self) -> Path:
        """真正被写入的节点；回读也走同一条。"""
        return self.path / "brightness"

    @property
    def level(self) -> int | None:
        """优先用内核回读值，它才是屏幕实际亮度。"""
        return self.actual if self.actual is not None else self.current


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return None


def read_int(path: Path) -> int | None:
    """只接受非负十进制；空文件与 `-1` 一律按读不到处理。"""
    raw = read_text(path)
    return int(raw) if raw is not None and raw.isdigit() else None


def scan(policy: Policy, target: Target) -> tuple[Device, ...]:
    """按目标扫描；ALL 返回屏幕与键盘两组拼接。"""
    found = ()
    if target in (Target.SCREEN, Target.ALL):
        found += _scan(policy.backlight, SCREEN)
    if target in (Target.KEYBOARD, Target.ALL):
        found += _scan(policy.leds, KBD)
    return found


def pick(policy: Policy, target: Target) -> tuple[Device, ...]:
    """按目标各取一台主设备；缺席的一边不出现在结果里。"""
    picked = []
    if target in (Target.SCREEN, Target.ALL):
        picked.append(primary(_scan(policy.backlight, SCREEN)))
    if target in (Target.KEYBOARD, Target.ALL):
        picked.append(primary(_scan(policy.leds, KBD)))
    return tuple(dev for dev in picked if dev is not None)


def primary(devs: tuple[Device, ...]) -> Device | None:
    return min(devs, key=rank) if devs else None


def rank(dev: Device) -> tuple[int, int, str]:
    """类型优先级 → 刻度上限（大的先）→ 名字，保证排序与目录次序无关。"""
    pos = len(TYPE_ORDER)
    if dev.type in TYPE_ORDER:
        pos = TYPE_ORDER.index(dev.type)
    return pos, -dev.maximum, dev.name


def _scan(root: str, kind: str) -> tuple[Device, ...]:
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return ()
    found = (_build(Path(root) / name, name, kind) for name in names)
    return tuple(dev for dev in found if dev is not None)


def _build(path: Path, name: str, kind: str) -> Device | None:
    maximum = read_int(path / "max_brightness")
    if maximum is None or maximum <= 0:
        return None
    if kind == KBD and KBD_SUFFIX not in name.lower():
        return None
    return Device(
        name=name,
        kind=kind,
        type=read_text(path / "type") or LEDS,
        maximum=maximum,
        current=read_int(path / "brightness"),
        actual=read_int(path / "actual_brightness"),
        path=path,
        writable=os.access(path / "brightness", os.W_OK),
    )
