# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 供电与热状态到档位的判定
#
# 档位选择只由四个输入决定：当前供电、最严重热区、电池电量、整机负载。热
# 状态优先于供电——电池上本来就已经降档，而在 AC 上过热时继续维持 balanced
# 会让固件把风扇与功耗墙都推到上限，这正是 285H 反复节流、卡死与重启的现场。
# 适配器在线但电池仍在放电时按 cool 处理：负载已经超出适配器能力，维持 AC
# 档只会让放电继续。电量优先于常规电池档：285H 现场是适配器未在线、电池
# 30% 仍在跑满核负载，这种放电速率下撑不到下一次充电，机器会以掉电关机收场，
# 系统侧看不到任何关机记录。没有热区读数的机器按 AC 处理，因为无法证明它
# 需要降档，误降档会直接牺牲桌面响应。
#
# 负载只影响 AC 侧：AC 上高负载不降档，而是把 EPP 从 balance_power 抬到
# balance_performance。理由不是「跑得更快」，而是「排得更空」——285H 的
# 长任务下 CPU 队列持续增长，省电侧 EPP 让每次突发都要重新爬频，排队时间
# 被放大成端到端延迟；抬 EPP 只是让爬频更快，功耗墙仍由固件按温度裁。电池
# 侧不因负载抬档，续航优先，掉电风险由 duty 的受控关机兜住。
#
# 评估与落地共用同一条链路：同一份采样、同一份档位、同一组控制点，只有
# 最后一步是否写 sysfs 不同。这样验收侧读到的结论与守护进程落地的结论
# 不可能分叉。
#
# 空闲深度的预算与档位同步决定：AC 且未过热时禁用退出延迟超过实时预算的
# 空闲态，其余情况恢复固件默认。理由与档位一致——过热时先把热排掉，电池上
# 先把电省下，两者都不该为了唤醒延迟而放弃降档。

"""AirymaxOS power guard."""

from __future__ import annotations

from . import cooling, idle, load, supply, thermal, throttle, tuning
from .duty import Duty, decide, enforce
from .policy import Policy, Tuning
from .report import Report
from .supply import LOW_CHARGE, Source
from .thermal import Severity

__all__ = ["apply", "assess", "choose", "inspect"]


def choose(source: Source, heat: Severity, charge: int | None,
           policy: Policy, strain: Severity = Severity.UNKNOWN) -> Tuning:
    if heat.rank >= Severity.HOT.rank:
        return policy.cool
    if source is Source.WEAK:
        return policy.cool
    if source is not Source.BATTERY:
        return policy.busy if strain.rank >= Severity.WARN.rank else policy.ac
    if charge is not None and charge <= LOW_CHARGE:
        return policy.cool
    return policy.battery


def assess(policy: Policy, push: bool) -> Report:
    source = supply.detect(policy)
    level = supply.charge(policy)
    heat = thermal.collect(policy)
    work = load.sample(policy)
    cool = cooling.collect(policy)
    speed = throttle.rate(throttle.measure(_total(heat)),
                          throttle.load(policy.state))
    action = decide(source, level)
    hot = thermal.peak(heat.severity, throttle.grade(speed))
    wanted = choose(source, hot, level, policy, work.strain)
    cap = _cap(source, hot)
    if push:
        knobs = tuning.apply(policy, wanted) + idle.apply(policy, cap)
        throttle.save(policy.state, _total(heat))
        load.alert(policy, work)
        _enforce(action, level, policy)
    else:
        knobs = tuning.inspect(policy, wanted) + idle.inspect(policy, cap)
    return Report(tuning.verdict(knobs), source, level, heat, knobs, work,
                  cool, speed, action)


def _cap(source: Source, heat: Severity) -> bool:
    """只有 AC 且未过热时才限制空闲深度。"""
    return source is Source.AC and heat.rank < Severity.HOT.rank


def _enforce(action: Duty, level: int | None, policy: Policy) -> None:
    if action is Duty.NONE or level is None:
        return
    enforce(action, level, policy)


def _total(heat: thermal.Thermal) -> int:
    return (heat.package or 0) + (heat.core or 0)


def inspect(policy: Policy) -> Report:
    """只读评估，供验收与巡检使用。"""
    return assess(policy, False)


def apply(policy: Policy) -> Report:
    """落地档位并回读，供守护单元使用。"""
    return assess(policy, True)
