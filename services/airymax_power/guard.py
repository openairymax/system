# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 供电与热状态到档位的判定
#
# 档位选择只由三个输入决定：当前供电、最严重热区、电池电量。热状态优先于
# 供电——电池上本来就已经降档，而在 AC 上过热时继续维持 balanced 会让固件
# 把风扇与功耗墙都推到上限，这正是 285H 反复节流、卡死与重启的现场。电量
# 优先于常规电池档：285H 现场是适配器未在线、电池 30% 仍在跑满核负载，这
# 种放电速率下撑不到下一次充电，机器会以掉电关机收场，系统侧看不到任何
# 关机记录。没有热区读数的机器按 AC 处理，因为无法证明它需要降档，误降档
# 会直接牺牲桌面响应。
#
# 评估与落地共用同一条链路：同一份采样、同一份档位、同一组控制点，只有
# 最后一步是否写 sysfs 不同。这样验收侧读到的结论与守护进程落地的结论
# 不可能分叉。

"""AirymaxOS power guard."""

from __future__ import annotations

from . import supply, thermal, tuning
from .policy import Policy, Tuning
from .report import Report
from .supply import LOW_CHARGE, Source
from .thermal import Severity

__all__ = ["apply", "assess", "choose", "inspect"]


def choose(source: Source, heat: Severity, charge: int | None,
           policy: Policy) -> Tuning:
    if heat.rank >= Severity.HOT.rank:
        return policy.cool
    if source is not Source.BATTERY:
        return policy.ac
    if charge is not None and charge <= LOW_CHARGE:
        return policy.cool
    return policy.battery


def assess(policy: Policy, push: bool) -> Report:
    source = supply.detect(policy)
    level = supply.charge(policy)
    heat = thermal.collect(policy)
    wanted = choose(source, heat.severity, level, policy)
    if push:
        knobs = tuning.apply(policy, wanted)
    else:
        knobs = tuning.inspect(policy, wanted)
    return Report(tuning.verdict(knobs), source, level, heat, knobs)


def inspect(policy: Policy) -> Report:
    """只读评估，供验收与巡检使用。"""
    return assess(policy, False)


def apply(policy: Policy) -> Report:
    """落地档位并回读，供守护单元使用。"""
    return assess(policy, True)
