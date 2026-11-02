# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 电源控制点的写入与回读
#
# 一台机器实际暴露哪些控制点并不固定：平台固件可能只有 platform_profile，
# 也可能只有 cpufreq 的 governor 与 EPP，两者都没有的笔记本同样存在。把
# 「接口不存在」「接口存在但目标值不在可选集合内」「写了但没生效」分清楚，
# 才能既不在缺失接口上误报失败，也不把写入被内核静默忽略当成成功。
#
# 判定一律以回读为准：写入返回 0 只说明内核收下了请求，governor 可能在
# 下一次频率采样时被其它守护进程改回去。写入顺序先固件档位、后逐 CPU 的
# governor 与 EPP，因为 platform_profile 会重置 EPP 的可用取值，反过来
# 则不会。

"""AirymaxOS power knobs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path

from . import sysfs
from .policy import Policy, Tuning

__all__ = ["Knob", "State", "apply", "inspect", "verdict"]

PROFILE = "platform_profile"

GOVERNOR = "scaling_governor"

GOV_SET = "scaling_available_governors"

EPP = "energy_performance_preference"

EPP_SET = "energy_performance_available_preferences"


@unique
class State(Enum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class Knob:
    """一个控制点的目标、现状与落地结果。"""

    name: str
    target: str | None
    current: str | None
    present: bool
    landed: bool


@dataclass(frozen=True, slots=True)
class _Spec:
    """一个控制点与它的候选目标值，候选按优先级排列。"""

    name: str
    wanted: tuple[str, ...]
    path: Path
    allow: Path | None


def inspect(policy: Policy, wanted: Tuning) -> tuple[Knob, ...]:
    """只读检查，不写任何节点。"""
    return tuple(_knob(spec) for spec in _specs(policy, wanted))


def apply(policy: Policy, wanted: Tuning) -> tuple[Knob, ...]:
    """写入未落地的控制点，再整体回读。"""
    for spec in _specs(policy, wanted):
        _push(spec)
    return inspect(policy, wanted)


def verdict(knobs: tuple[Knob, ...]) -> State:
    live = [knob for knob in knobs if knob.present]
    if not live:
        return State.UNSUPPORTED
    landed = sum(1 for knob in live if knob.landed)
    if landed == len(live):
        return State.OK
    return State.PARTIAL if landed else State.FAILED


def _specs(policy: Policy, wanted: Tuning) -> tuple[_Spec, ...]:
    specs = [
        _Spec(PROFILE, (wanted.profile,), Path(policy.profile),
              Path(policy.choices)),
    ]
    for entry in _policies(Path(policy.cpufreq)):
        specs.append(_Spec(f"{entry.name}/governor", wanted.governors,
                           entry / GOVERNOR, entry / GOV_SET))
        specs.append(_Spec(f"{entry.name}/epp", wanted.epps,
                           entry / EPP, entry / EPP_SET))
    return tuple(specs)


def _policies(root: Path) -> list[Path]:
    try:
        return sorted(root.glob("policy*"), key=sysfs.order)
    except OSError:
        return []


def _knob(spec: _Spec) -> Knob:
    """目标值按当前可选集合解析，解析结果与回读值一起构成证据。

    每次调用都重新读可选集合：platform_profile 的写入会改变 EPP 的可用
    取值，先解析后写入就会拿到过期的候选表。只有节点不存在才算本机不
    适用；节点存在但候选值全都不可用时，意图无法达成，必须计入「未落地」
    而不是悄悄从判定里消失。
    """
    current = sysfs.read_text(spec.path)
    if current is None:
        return Knob(spec.name, None, None, False, False)
    target = _target(spec)
    return Knob(spec.name, target, current, True,
                target is not None and current == target)


def _target(spec: _Spec) -> str | None:
    """取第一个受本机支持的目标值；无可选集合时按首选假定支持。"""
    if spec.allow is None:
        return spec.wanted[0]
    text = sysfs.read_text(spec.allow)
    if text is None:
        return spec.wanted[0]
    offered = set(text.split())
    return next((value for value in spec.wanted if value in offered), None)


def _push(spec: _Spec) -> None:
    knob = _knob(spec)
    if knob.target is not None and not knob.landed:
        sysfs.write_text(spec.path, knob.target)
