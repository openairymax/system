# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 空闲态深度与实时预算
#
# 285H 现场实测：C3_ACPI 的 exit_latency 是 1048µs，已经超过 0.0.4 软实时
# 上限 1000µs。只要有一个核停进 C3，唤醒路径就要先付 1048µs 的退出延迟，
# airy-rt-lat 的实测尖峰因此稳定落在 900µs 附近，超过预算即判失败；把该
# 状态禁用后同一台机器的最大延迟回到 32~408µs。空闲深度与 governor、EPP
# 是同一层的控制点：前者决定「睡着多久」，后者决定「醒来多快」，只调后者
# 无法把一次深睡的退出成本拉回预算内。
#
# 预算只在 AC 且未过热时生效。电池上禁用深空闲会明显抬高待机功耗，抵消
# 降档省下的电；已经进入 hot 时禁用深空闲则会让核持续发热，与降档的意图
# 相反。两种情况都恢复固件默认，把选择权交回平台。判定以回读为准：写入
# disable 只说明内核收下请求，是否真的不再进入该状态由下一次读回的 disable
# 值决定。
#
# 逐核出一个控制点，而不是整机一个：同型号机器上各核的 cpuidle 目录可以
# 不同（热插拔、固件差异），合成一条会让「哪个核没落上」无处可查。

"""AirymaxOS idle depth control."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import sysfs
from .policy import Policy
from .tuning import Knob

__all__ = ["BUDGET_US", "apply", "inspect"]

BUDGET_US = 1000

LABEL = "idle"

DISABLE = "disable"

FREEDOM = "firmware"


@dataclass(frozen=True, slots=True)
class _State:
    """一个 cpuidle 状态的名字、退出延迟与禁用开关。"""

    name: str
    latency: int
    disabled: bool
    path: Path


def inspect(policy: Policy, cap: bool) -> tuple[Knob, ...]:
    """只读检查每个核的空闲深度是否落在预算内。"""
    return tuple(_knob(cpu, states, cap)
                 for cpu, states in _cpus(policy) if states)


def apply(policy: Policy, cap: bool) -> tuple[Knob, ...]:
    """写入需要调整的 disable 开关，再整体回读。"""
    for _, states in _cpus(policy):
        for state in states:
            _push(state, cap)
    return inspect(policy, cap)


def _cpus(policy: Policy) -> list[tuple[str, tuple[_State, ...]]]:
    try:
        entries = sorted(Path(policy.cpus).glob("cpu[0-9]*"), key=sysfs.order)
    except OSError:
        return []
    return [(entry.name, _states(entry)) for entry in entries]


def _states(cpu: Path) -> tuple[_State, ...]:
    """读一个核的全部空闲态；缺字段的条目跳过，不猜默认值。"""
    try:
        dirs = sorted((cpu / "cpuidle").glob("state*"), key=sysfs.order)
    except OSError:
        return ()
    states = []
    for entry in dirs:
        name = sysfs.read_text(entry / "name")
        latency = sysfs.read_int(entry / "latency")
        disable = sysfs.read_int(entry / DISABLE)
        if name is None or latency is None or disable is None:
            continue
        states.append(_State(name, latency, bool(disable),
                             entry / DISABLE))
    return tuple(states)


def _knob(cpu: str, states: tuple[_State, ...], cap: bool) -> Knob:
    target = f"<={BUDGET_US}us" if cap else FREEDOM
    return Knob(f"{cpu}/{LABEL}", target, _deepest(states), True,
                _landed(states, cap))


def _deepest(states: tuple[_State, ...]) -> str:
    """当前允许进入的最深状态，直接回答「还会睡多久」。"""
    live = [state for state in states if not state.disabled]
    if not live:
        return "off"
    deep = max(live, key=lambda state: state.latency)
    return f"{deep.name} {deep.latency}us"


def _landed(states: tuple[_State, ...], cap: bool) -> bool:
    if not cap:
        return not any(state.disabled for state in states)
    return all(state.disabled or state.latency <= BUDGET_US
               for state in states)


def _push(state: _State, cap: bool) -> None:
    if cap and not state.disabled and state.latency > BUDGET_US:
        sysfs.write_text(state.path, "1")
    elif not cap and state.disabled:
        sysfs.write_text(state.path, "0")
