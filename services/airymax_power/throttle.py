# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 节流计数器增速
#
# 285H 现场取证：SEN1 只有 48°C、platform_profile 与 governor 全停在
# performance，而 package_throttle_count 在一次负载期间增长数万次。温度读数
# 看不到功耗墙与电流墙的节流——PRD/PL 触发的降频发生时，热区温度可以远低于
# critical。只看温度就会把「正在被固件限速」误判成「热余量充足」，这正是
# 机器反复卡死与重启却始终没有降档的直接原因。
#
# 因此以两次采样之间的节流增量作为独立判据：只看增量不看累计值，重启后计数
# 归零不会误报，历史累计也不会让机器永远停在降档档位。采样落在 /run（tmpfs，
# 重启即清空），由守护单元在每次落地后更新；只读巡检不写采样，连续两次巡检
# 不会把一次真实的降档消费掉。

"""AirymaxOS throttle rate."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from .thermal import Severity

__all__ = [
    "RATE_HOT",
    "RATE_WARN",
    "Sample",
    "grade",
    "load",
    "measure",
    "rate",
    "save",
]

RATE_HOT = 2.0

RATE_WARN = 0.2


@dataclass(frozen=True, slots=True)
class Sample:
    """一次节流计数采样；时间戳取 CLOCK_MONOTONIC。"""

    count: int
    stamp: float


def measure(count: int) -> Sample:
    """给当前计数打上单调时钟时间戳。"""
    return Sample(count, time.monotonic())


def load(path: str) -> Sample | None:
    """读取上次采样；文件缺失或损坏时返回 None，按首次采样处理。"""
    try:
        count, stamp = Path(path).read_text("ascii").split()
        return Sample(int(count), float(stamp))
    except (OSError, ValueError):
        return None


def save(path: str, count: int) -> None:
    """写入本次采样；写不进去不影响判定，下次按首次采样处理。"""
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{count} {time.monotonic():.3f}\n", "ascii")
    except OSError:
        pass


def rate(now: Sample, prev: Sample | None) -> float | None:
    """两次采样之间的每秒节流次数；无法比较时返回 None。"""
    if prev is None or now.stamp <= prev.stamp or now.count < prev.count:
        return None
    return (now.count - prev.count) / (now.stamp - prev.stamp)


def grade(value: float | None) -> Severity:
    """按增速分级；没有基线时不作判定，避免首轮误降档。"""
    if value is None:
        return Severity.UNKNOWN
    if value >= RATE_HOT:
        return Severity.HOT
    if value >= RATE_WARN:
        return Severity.WARN
    return Severity.OK