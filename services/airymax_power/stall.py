# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 整机停顿取证
#
# 285H 跑本地模型长任务时会整机停住两三分钟：SSH 无响应、ICMP 全丢，恢复后
# uptime 连续、没有 panic、没有 MCE、没有新的 OOM 记录。这是内存回收与分配
# 打转时整机被挡住，不是某个进程死了——死掉的进程不会让内核连网络栈都停下。
#
# 这种停顿没有留下任何一条日志，因为能写日志的进程当时也在等。唯一还在动的
# 事实是「本该按点醒来的周期任务没有按点醒来」：定时器每 60 s 跑一次，某次
# 醒来发现距上次已经过去 200 s，这 140 s 就是停顿的下界。不需要额外采样器，
# 不占 CPU，也不会因为一次采样本身太重而制造它要测量的现象。
#
# 时钟用 CLOCK_BOOTTIME 而不是 CLOCK_MONOTONIC：挂起对用户而言与卡死同样是
# 「机器不动了」，两者都该记进同一份证据。判据留出 30 s 余量覆盖定时器的
# AccuracySec 与一次巡检自身的耗时，避免把正常调度抖动报成停顿。
#
# 只记录不动作。停顿已经过去，此刻写任何东西都是二次扰动；证据落到 /run，
# 重启即清空——重启本身由 boot 记录解释，不需要这份文件兜底。

"""AirymaxOS stall evidence."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import sysfs
from .policy import Policy

__all__ = ["BUDGET", "Pause", "recent", "watch"]

INTERVAL = 60.0

SLACK = 30.0

BUDGET = INTERVAL + SLACK


@dataclass(frozen=True, slots=True)
class Pause:
    """一次停顿：实际间隔、超出预算的部分与发生时刻。"""

    gap: float
    over: float
    at: str

    def dump(self) -> dict[str, object]:
        return {
            "gap": round(self.gap, 1),
            "over": round(self.over, 1),
            "at": self.at,
        }


def watch(policy: Policy, now: float | None = None) -> Pause | None:
    """记录一次心跳；超出预算时返回本次停顿并把证据写进 /run。"""
    stamp = _now() if now is None else now
    prev = _read(policy.beat)
    _write(policy.beat, f"{stamp:.3f}\n")
    if prev is None:
        return None
    gap = stamp - prev
    if gap <= BUDGET:
        return None
    found = Pause(gap, gap - BUDGET, _stamp())
    _write(policy.pause, f"{found.gap:.1f} {found.over:.1f} {found.at}\n")
    return found


def recent(policy: Policy) -> Pause | None:
    """读取最近一次停顿；没有记录或记录损坏时返回 None。"""
    text = sysfs.read_text(Path(policy.pause))
    if text is None:
        return None
    fields = text.split()
    if len(fields) != 3:
        return None
    try:
        gap, over = float(fields[0]), float(fields[1])
    except ValueError:
        return None
    return Pause(gap, over, fields[2])


def _now() -> float:
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path: str) -> float | None:
    text = sysfs.read_text(Path(path))
    if text is None:
        return None
    try:
        return float(text.strip())
    except ValueError:
        return None


def _write(path: str, text: str) -> None:
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="ascii")
    except OSError:
        pass