# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 新建 cgroup 的实时带宽默认值
#
# 285H 现场：四个 slice 都已写入 190000 us，`airy-rt-cap status` 报 ok，
# 但交付账户里 `chrt -r 10 true` 仍然 EPERM。三个门禁各自独立，缺任一条
# 都表现为同一个 EPERM，只看其中一个必然误判：
#
#   1. 全局 kernel.sched_rt_runtime_us 为 0 或不可读——实时带宽整体关闭；
#   2. 任务**直属** cgroup 的 cpu.rt_runtime_us 为 0——内核只看直属组，
#      父组有预算不会继承下来；
#   3. RLIMIT_RTPRIO 为 0 且进程没有 CAP_SYS_NICE——与 cgroup 无关，
#      root 因能力通过、普通账户被拒（本机 `ulimit -r` 为 0 时可复现）。
#
# 内核侧另有两条事实必须如实报出而不是「尽量修」：
#
#   * alloc_rt_sched_group() 用 init_rt_bandwidth(..., 0) 初始化，新建
#     task_group 的预算固定为 0，不继承父组；systemd 管理的单元是否补写
#     取决于版本与 CPUAccounting，285H 的会话组实测未开记账，因此没有
#     补写。这一条登记在 KRN-01，distro 层改不了。
#   * 全局预算有限时给组写 -1 会被 tg_rt_schedulable() 以 EINVAL 拒绝，
#     因为 RUNTIME_INF 的带宽上限超过全局；只有全局本身是 -1 才允许。
#     而全局 -1 会让 start_rt_bandwidth() 与 update_curr_rt() 全部不记账，
#     X-01 的 per-slice 预算随之失效。所以补齐只能写有限值，取父组同额。
#
# 因此本模块做两件事：defaults 只读判读三道门禁并给出阻塞项；seed 对
# 新建组逐组补写有限预算，绝不写 -1。两者共用 cgroup 布局识别，判定
# 口径与 status/apply 一致。
#
# 设计依据：docs-linux/170-performance/07-rt-matrix.md §4.1、
# docs-linux/100-operations/02-configuration.md §2.2。

"""AirymaxOS RT bandwidth defaults and seeding (RT-01)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import cgroup, sysctl
from .cgroup import Kind, Layout
from .policy import Policy

__all__ = [
    "ABSENT",
    "BLOCKED",
    "BUDGETED",
    "DEFAULT_LIMITS",
    "GLOBAL_READ",
    "GLOBAL_ZERO",
    "GROUP_ABSENT",
    "GROUP_ZERO",
    "KEPT",
    "NEW_GROUP_ZERO",
    "NO_ACCOUNT",
    "NO_CGROUP",
    "RTPRIO_ZERO",
    "SEEDED",
    "UNKNOWN",
    "UNLIMITED",
    "Defaults",
    "Seed",
    "advice",
    "inspect",
    "rlimit",
    "seed",
    "seed_subtree",
]

DEFAULT_LIMITS = "/proc/self/limits"

RTPRIO_KEY = "Max realtime priority"

UNLIMITED = "unlimited"

BUDGETED = "budgeted"

BLOCKED = "blocked"

UNKNOWN = "unknown"

GLOBAL_READ = "global-unreadable"

GLOBAL_ZERO = "global-zero"

NO_CGROUP = "no-cgroup"

NO_ACCOUNT = "no-accounting"

GROUP_ABSENT = "group-absent"

GROUP_ZERO = "group-zero"

NEW_GROUP_ZERO = "new-group-zero"

RTPRIO_ZERO = "rtprio-zero"

SEEDED = "seeded"

KEPT = "kept"

ABSENT = "absent"

ADVICE = {
    UNLIMITED: "内核不限实时带宽，无需切分",
    BUDGETED: "本组已有有限预算，实时任务可启动",
    BLOCKED: "预算或 RLIMIT_RTPRIO 为 0：先 seed --group 补写，再提权",
    UNKNOWN: "接口不可读或记账树缺失：确认挂载与内核 RT 组调度",
}


@dataclass(frozen=True, slots=True)
class Defaults:
    """一次默认值判读；``blocked`` 列出真正挡住启动的门禁。"""

    kind: Kind | None
    total: int | None
    period: int | None
    enabled: bool
    root: str | None
    group: str
    fresh: str | None
    rtprio: int | None
    privileged: bool
    blocked: tuple[str, ...]
    verdict: str

    @property
    def usable(self) -> bool:
        """本方判定为可启动实时任务。"""
        return self.verdict in (UNLIMITED, BUDGETED)

    def dump(self) -> dict[str, object]:
        """命令行 --json 的唯一输出形状。"""
        return {
            "kind": self.kind.value if self.kind else "",
            "total": self.total,
            "period": self.period,
            "enabled": self.enabled,
            "root": self.root,
            "group": self.group,
            "fresh": self.fresh,
            "rtprio": self.rtprio,
            "privileged": self.privileged,
            "blocked": list(self.blocked),
            "verdict": self.verdict,
        }


@dataclass(frozen=True, slots=True)
class Seed:
    """单个组的补写结果；``want`` 为 0 表示没有可写的有限值。"""

    group: str
    want: int
    have: int | None
    state: str
    note: str = ""

    def dump(self) -> dict[str, object]:
        return {
            "group": self.group,
            "want": self.want,
            "have": self.have,
            "state": self.state,
            "note": self.note,
        }


def advice(verdict: str) -> str:
    """判读结论对应的处置建议。"""
    return ADVICE.get(verdict, ADVICE[UNKNOWN])


def rlimit(path: str = DEFAULT_LIMITS) -> int | None:
    """读 RLIMIT_RTPRIO；``unlimited`` 与不可读都返回 None。

    两个 None 的后果不同，但都意味着「这一道门禁不拦本次判定」：文件读不到
    时无法断言被拦，如实标为不可读而不是报 0；``unlimited`` 本身就是放行。
    """
    try:
        text = Path(path).read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if not line.startswith(RTPRIO_KEY):
            continue
        try:
            return int(line.split()[-1], 10)
        except (IndexError, ValueError):
            return None
    return None


def inspect(
    policy: Policy,
    group: str = "",
    limits: str = DEFAULT_LIMITS,
    privileged: bool | None = None,
) -> Defaults:
    """只读判读三道门禁，不写任何接口。

    ``group`` 为空时判读「新建组」：内核不继承父组预算，可记账的布局上
    新建组固定是 0。这里不伪造读取结果，而是用 ``new-group-zero`` 单独
    标出这一条，交由 KRN-01 追踪；指定 ``--group`` 时读该组的真实值。
    """
    found = sysctl.budget(policy)
    layout = cgroup.detect(policy.cgroup_root)
    rtprio = rlimit(limits)
    if privileged is None:
        privileged = os.geteuid() == 0
    total = found.total if found is not None else None
    gates = _gates(found is None, total, layout, group, rtprio, privileged)
    enabled = layout is not None and layout.supported
    fresh = None
    if layout is not None and group and found is not None and found.total > 0:
        fresh = cgroup.read_raw(layout, group)
        if fresh is None:
            gates.append(GROUP_ABSENT)
        elif _zero(fresh):
            gates.append(GROUP_ZERO)
    return Defaults(
        kind=layout.kind if layout is not None else None,
        total=total,
        period=found.period if found is not None else None,
        enabled=enabled,
        root=cgroup.read_raw(layout, "") if layout is not None else None,
        group=group,
        fresh=fresh,
        rtprio=rtprio,
        privileged=privileged,
        blocked=tuple(dict.fromkeys(gates)),
        verdict=_verdict(found is None, total, layout, gates),
    )


def _gates(
    unreadable: bool,
    total: int | None,
    layout: Layout | None,
    group: str,
    rtprio: int | None,
    privileged: bool,
) -> list[str]:
    """逐个列出挡住启动的门禁；不限全局预算时记账类门禁不算阻塞。"""
    if unreadable:
        return [GLOBAL_READ] + _prio(rtprio, privileged)
    if total is not None and total == 0:
        return [GLOBAL_ZERO] + _prio(rtprio, privileged)
    if total is not None and total < 0:
        return _prio(rtprio, privileged)
    gates: list[str] = []
    if layout is None:
        gates.append(NO_CGROUP)
    elif not layout.supported:
        gates.append(NO_ACCOUNT)
    elif not group:
        gates.append(NEW_GROUP_ZERO)
    return gates + _prio(rtprio, privileged)


def _prio(rtprio: int | None, privileged: bool) -> list[str]:
    """RLIMIT_RTPRIO 门禁：root 有 CAP_SYS_NICE，不受该限制。"""
    if not privileged and rtprio == 0:
        return [RTPRIO_ZERO]
    return []


def _zero(token: str) -> bool:
    """``0`` 是禁止；``-1`` 与 ``max`` 是不限，不算零预算。"""
    try:
        return int(token, 10) <= 0
    except ValueError:
        return False


def _verdict(
    unreadable: bool,
    total: int | None,
    layout: Layout | None,
    gates: list[str],
) -> str:
    """全局不限时实时可用；全局有限时任一门禁命中即 blocked。"""
    if unreadable or total is None:
        return UNKNOWN
    if total < 0:
        return BLOCKED if RTPRIO_ZERO in gates else UNLIMITED
    if total == 0:
        return BLOCKED
    if layout is None:
        return UNKNOWN
    return BLOCKED if gates else BUDGETED


def seed(
    policy: Policy,
    groups: tuple[str, ...],
) -> tuple[Seed, ...]:
    """给新建组补写有限预算；已非 0 的组一律跳过。

    补写值取父组同额：层级预算要求子组之和不超过父组，同额对单个子组
    必然合法，也不改变父组已经切好的份额。顶层 slice 的父组是记账根，
    按策略份数取 ``policy.share(total)``，正好留出未列出的那 1 份余量。
    绝不写 ``-1``：全局有限时内核会以 EINVAL 拒绝，见模块头部说明。
    """
    found = sysctl.budget(policy)
    layout = cgroup.detect(policy.cgroup_root)
    if found is None or layout is None or not layout.supported:
        note = _absence(found is None, layout)
        return tuple(Seed(name, 0, None, ABSENT, note) for name in groups)
    if found.total < 0:
        return tuple(
            Seed(name, 0, cgroup.read(layout, name), KEPT, "内核不限预算")
            for name in groups
        )
    if found.total == 0:
        return tuple(
            Seed(name, 0, cgroup.read(layout, name), BLOCKED, "全局预算为 0")
            for name in groups
        )
    return tuple(
        _seed_one(policy, layout, found.total, found.period, name)
        for name in groups
    )


def seed_subtree(
    policy: Policy,
    name: str,
    depth: int = 1,
) -> tuple[Seed, ...]:
    """补写某个 slice 下已存在的子树；记账树缺失时如实报 absent。"""
    layout = cgroup.detect(policy.cgroup_root)
    if layout is None:
        return seed(policy, (name,))
    return seed(policy, cgroup.subgroups(layout, name, depth))


def _seed_one(
    policy: Policy,
    layout: Layout,
    total: int,
    period: int,
    name: str,
) -> Seed:
    have = cgroup.read(layout, name)
    if have is not None and have > 0:
        return Seed(name, have, have, KEPT)
    wants = _want(policy, layout, total, name)
    if wants <= 0:
        return Seed(name, 0, have, BLOCKED, "父组没有可分配的有限预算")
    if not cgroup.write(layout, name, wants, period):
        note = "写入未生效：父组剩余预算不足或目录不可写"
        return Seed(name, wants, cgroup.read(layout, name), BLOCKED, note)
    return Seed(name, wants, cgroup.read(layout, name), SEEDED)


def _want(policy: Policy, layout: Layout, total: int, name: str) -> int:
    """补写目标：嵌套组取父组同额，顶层取策略份额。"""
    if "/" not in name:
        return policy.share(total)
    parent = cgroup.read(layout, name.rsplit("/", 1)[0])
    if parent is None or parent <= 0:
        return policy.share(total)
    return parent


def _absence(unreadable: bool, layout: Layout | None) -> str:
    if unreadable:
        return "总预算接口不可读"
    if layout is None:
        return "未找到 cgroup cpu 记账树"
    return "内核未启用 RT 组调度"
