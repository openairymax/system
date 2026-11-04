# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 背光策略的单点定义
#
# 285H 笔记本实测：`/sys/class/backlight` 下同时存在 firmware 型的
# `intel_backlight` 与 raw 型的面板节点；键盘背光只在
# `/sys/class/leds/*::kbd_backlight` 出现。0.0.3 之前系统没有任何用户态
# 入口读写这两处，换机型或接外接屏后只能靠 `brightnessctl` 猜设备名，
# 猜错就把亮度写到一条不生效的通路上。
#
# 三条取舍写在这里，不散落到调用方：
#   ① 屏幕下限 1%：写 0 是直接黑屏，远程或无人值守时再也调不回来；要真的
#      关屏必须显式传 `--floor 0`，让「关屏」成为一个被说出口的动作。
#   ② 键盘下限 0：关掉键盘背光本身是合法用法，不应被下限挡住。
#   ③ 同型设备排序 firmware > platform > raw，其次比 max_brightness，
#      最后比名字：firmware 型由固件换算，raw 型是厂商自报刻度，同一块
#      面板常同时暴露两者，写 raw 那一路在部分机型上不生效。

"""AirymaxOS backlight policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "DEFAULT_BACKLIGHT",
    "DEFAULT_LEDS",
    "KBD_SUFFIX",
    "MARK",
    "SCREEN_FLOOR",
    "TYPE_ORDER",
    "Policy",
    "Target",
]

DEFAULT_BACKLIGHT = "/sys/class/backlight"

DEFAULT_LEDS = "/sys/class/leds"

MARK = "AIRYMAX-BRIGHT"

SCREEN_FLOOR = 1

KBD_SUFFIX = "kbd_backlight"

TYPE_ORDER = ("firmware", "platform", "raw")


class Target(str, Enum):
    """一次调节要覆盖的目标。ALL 是屏幕与键盘各取一台主设备。"""

    SCREEN = "screen"
    KEYBOARD = "keyboard"
    ALL = "all"


@dataclass(frozen=True, slots=True)
class Policy:
    """两棵 sysfs 子树与屏幕下限；测试与救援环境整体改指临时目录。"""

    backlight: str = DEFAULT_BACKLIGHT
    leds: str = DEFAULT_LEDS
    floor: int = SCREEN_FLOOR
