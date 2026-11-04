# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 百分比与原始刻度的换算
#
# 背光只有一条可写节点 `brightness`，它吃的是厂商刻度而不是百分比：屏幕
# 常见 0..1000 或 0..937，键盘常常只有 0..2。所有接口对外只说百分比，
# 换算集中在这里，避免每个调用点各写一遍四舍五入而在边界上打架。
#
# 取整方向按安全侧：向下限靠拢时向上取整（至少 1 格），因为把 1% 算成 0
# 就是黑屏；上限方向直接夹到 max_brightness，写超会被内核静默截断，宁可
# 自己先夹住再回读。

"""AirymaxOS backlight level arithmetic."""

from __future__ import annotations

__all__ = [
    "MAX_PCT",
    "MIN_PCT",
    "clamp",
    "floor_raw",
    "pct_of",
    "raw_of",
    "shift",
]

MIN_PCT = 0

MAX_PCT = 100


def clamp(pct: int, floor: int) -> int:
    """把百分比夹到 [floor, 100]。"""
    return max(floor, min(MAX_PCT, pct))


def floor_raw(maximum: int, floor: int) -> int:
    """下限对应的最小刻度；floor 为 0 时允许真的关掉。"""
    if floor <= 0:
        return 0
    return max(1, -(-maximum * floor // 100))


def raw_of(pct: int, maximum: int, floor: int) -> int:
    """百分比换算成刻度，再按下限与上限各夹一次。"""
    want = (clamp(pct, floor) * maximum + 50) // 100
    return max(floor_raw(maximum, floor), min(maximum, want))


def pct_of(raw: int, maximum: int) -> int:
    """刻度换算成百分比，四舍五入到整数；上限为 0 时按 0 处理。"""
    if maximum <= 0:
        return MIN_PCT
    return (raw * 200 + maximum) // (maximum * 2)


def shift(pct: int, delta: int, floor: int) -> int:
    """在当前百分比上走一步，结果仍落在 [floor, 100]。"""
    return clamp(pct + delta, floor)
