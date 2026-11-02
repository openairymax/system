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
    target: str
    current: str | None
    present: bool
    landed: bool


@dataclass(frozen=True, slots=True)
class _Spec:
    name: str
    target: str
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
        _Spec(PROFILE, wanted.profile, Path(policy.profile),
              Path(policy.choices)),
    ]
    for entry in _policies(Path(policy.cpufreq)):
        specs.append(_Spec(f"{entry.name}/governor", wanted.governor,
                           entry / GOVERNOR, entry / GOV_SET))
        specs.append(_Spec(f"{entry.name}/epp", wanted.epp,
                           entry / EPP, entry / EPP_SET))
    return tuple(specs)


def _policies(root: Path) -> list[Path]:
    try:
        return sorted(root.glob("policy*"), key=sysfs.order)
    except OSError:
        return []


def _knob(spec: _Spec) -> Knob:
    current = sysfs.read_text(spec.path)
    present = current is not None and _allowed(spec.allow, spec.target)
    return Knob(spec.name, spec.target, current, present,
                present and current == spec.target)


def _allowed(allow: Path | None, target: str) -> bool:
    """目标值不在内核给出的可选集合内时，这个控制点对本机不适用。"""
    if allow is None:
        return True
    text = sysfs.read_text(allow)
    return text is None or target in text.split()


def _push(spec: _Spec) -> None:
    knob = _knob(spec)
    if knob.present and not knob.landed:
        sysfs.write_text(spec.path, spec.target)
