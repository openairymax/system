# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 低电量动作：广播告警与受控关机
#
# 285H 现场的「意外重启」没有 panic、没有 MCE、没有 OOM，也没有任何
# 关机记录：适配器未在线，电池以约 30W 放电，电量耗尽时机器直接断电。
# 降档只能把这一刻推后，不能取消它；系统必须在电量见底前自己把机器关
# 干净，否则每一次现场掉电都是一次非正常关机，文件系统与取证日志都处在
# 风险里。
#
# 动作只在电池或适配器功率不足时发生：插电时电量低是充电过程，关机只会
# 把一台正在补电的机器关掉。电量读不到时不做任何动作——没有证据就不能
# 执行不可逆操作。动作状态写进 /run，重复评估只在等级变化时广播一次，
# 避免每分钟刷屏；受控关机每次评估都请求，它本身是幂等的。
#
# 判定与执行分开：inspect 只报告将要发生的动作，apply 才真正调用命令。
# 验收侧因此在任何机器上都能复算结论，而不会把巡检变成关机。

"""AirymaxOS low battery duty."""

from __future__ import annotations

from enum import Enum, unique

from . import notify
from .policy import Policy
from .supply import LOW_CHARGE, Source

__all__ = ["CRITICAL_CHARGE", "Duty", "decide", "enforce"]

CRITICAL_CHARGE = 5

MESSAGE = "AirymaxOS 电量不足 %d%%，请插电并保存工作；%d%% 将自动关机"


@unique
class Duty(Enum):
    NONE = "none"
    NOTIFY = "notify"
    POWEROFF = "poweroff"


def decide(source: Source, charge: int | None) -> Duty:
    """按供电来源与电量决定动作；非电池场景一律不动。"""
    if source not in (Source.BATTERY, Source.WEAK) or charge is None:
        return Duty.NONE
    if charge <= CRITICAL_CHARGE:
        return Duty.POWEROFF
    return Duty.NOTIFY if charge <= LOW_CHARGE else Duty.NONE


def enforce(duty: Duty, charge: int, policy: Policy) -> None:
    """执行动作；广播只在等级变化时发一次，关机本身是幂等的。"""
    if duty is Duty.NONE:
        return
    first = notify.last(policy.duty) != duty.value
    notify.store(policy.duty, duty.value)
    if duty is Duty.POWEROFF:
        notify.call(policy.poweroff, ("poweroff", "--no-block"))
    elif first:
        notify.call(policy.wall, (MESSAGE % (charge, CRITICAL_CHARGE),))
