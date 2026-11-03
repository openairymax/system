# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 处理器性能上限与滞回
#
# 285H 现场普查：intel_pstate 处于 active 模式，唯一能连续调节的执行器是
# max_perf_pct 与 no_turbo。platform_profile、governor 与 EPP 都是离散档位，
# 换个档就走完了可用空间；温度继续往上爬时，固件只剩两个选择——把风扇推到
# 上限，或者直接压功耗墙。package_throttle_count 在一次负载里增长上万次，
# 说明机器长期停在这条边界上，档位本身并不会把功耗拉回来。
#
# 因此把「功耗上限」独立成一维：按严重度分四阶给出 max_perf_pct 与 no_turbo，
# 与档位同时落地。段位只由输入决定（温度余量、节流增速、整机负载三者取最
# 严重），电池侧整体压一阶——同样温度下电池要同时扛续航与散热，不能让上限
# 停在 AC 的取值。
#
# 上限只降不弹：升一阶要连续 RECOVER 次评估都给出更宽松的结论才生效。降档
# 立即落地，因为热惯性远大于一次采样的抖动；而升档不能跟着一次读数回弹，
# 否则一台在阈值附近震荡的机器会每 30 秒在 100% 与 70% 之间来回跳，频率
# 反复爬升本身就在制造新的热量。
#
# 状态文件落在 /run（tmpfs，重启即清空）：重启后第一次评估按当前结论直接
# 落地，不会带着上一个内核周期的档位做无意义的等待。文件中同时记下连续
# 宽松次数，让「为什么还没升档」在报告里可查，而不是只留在进程内存里。

"""AirymaxOS processor ceiling."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .policy import Policy
from .thermal import Severity

__all__ = [
    "Ceiling",
    "LADDER",
    "RECOVER",
    "decide",
    "load",
    "of",
    "save",
]

RECOVER = 3

PASSIVE = 1


@dataclass(frozen=True, slots=True)
class Ceiling:
    """一阶性能上限：rung 是阶位（0 最紧），cap 与 turbo 是对应取值。"""

    rung: int
    cap: int
    turbo: int
    strikes: int = 0


LADDER = (
    Ceiling(0, 50, 0),
    Ceiling(1, 70, 0),
    Ceiling(2, 90, PASSIVE),
    Ceiling(3, 100, PASSIVE),
)

_RUNGS = {
    Severity.CRITICAL: 0,
    Severity.HOT: 1,
    Severity.WARN: 2,
    Severity.OK: 3,
    Severity.UNKNOWN: 3,
}


def of(level: Severity, tight: bool = False) -> Ceiling:
    """把严重度折算成上限阶位；tight 表示按电池侧再压一阶。"""
    rung = _RUNGS[level]
    if tight:
        rung = max(rung - 1, 0)
    return LADDER[rung]


def decide(policy: Policy, wanted: Ceiling) -> Ceiling:
    """叠加滞回：收紧立即生效，放宽需连续 RECOVER 次评估。"""
    prev = load(policy.ceil)
    if prev is None or wanted.rung <= prev.rung:
        return wanted
    strikes = prev.strikes + 1
    if strikes >= RECOVER:
        return wanted
    return Ceiling(prev.rung, prev.cap, prev.turbo, strikes)


def load(path: str) -> Ceiling | None:
    """读取上一轮结论；文件缺失或损坏时按首次评估处理。"""
    try:
        rung, strikes = Path(path).read_text("ascii").split()
        found = LADDER[int(rung)]
        return Ceiling(found.rung, found.cap, found.turbo, int(strikes))
    except (OSError, ValueError, IndexError):
        return None


def save(path: str, found: Ceiling) -> None:
    """记录本轮结论；写不进去不影响落地，下一轮按首次评估处理。"""
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{found.rung} {found.strikes}\n", "ascii")
    except OSError:
        pass
