# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 实时带宽的巡检与落地
#
# 巡检与落地共用同一条路径：都先读内核总预算、再识别布局、再逐 slice
# 回读。区别只在落地时多一次写入。这样「装机后看现在什么状态」与
# 「启动时把预算铺下去」不会出现两套判定口径——历史上正是两套脚本各自
# 判各自的，导致启动时写成功、门禁读失败，谁也说不清哪个是真的。
#
# 落地以回读为准，不以写入返回值为准；回读失败计入 missing，最终由
# :func:`airymax_rt.report.Report` 汇总成 partial 或 failed。

"""AirymaxOS RT bandwidth inspection and application (X-01)."""

from __future__ import annotations

from . import cgroup, sysctl
from .allocation import Entry, allocate
from .cgroup import Layout
from .policy import Policy
from .report import Landing, Report, State

__all__ = ["apply", "inspect"]


def inspect(policy: Policy) -> Report:
    """只读判定当前预算是否已经落地，不写任何接口。"""
    return _collect(policy, push=False)


def apply(policy: Policy) -> Report:
    """把计划写入 cgroup，并以回读结果作为成功判据。"""
    return _collect(policy, push=True)


def _collect(policy: Policy, push: bool) -> Report:
    found = sysctl.budget(policy)
    if found is None:
        return Report(State.NO_CPU, 0, 0, 0, None, ())
    layout = cgroup.detect(policy.cgroup_root)
    share = policy.share(found.total)
    landings = []
    for entry in allocate(policy, found.total):
        if push and layout is not None and layout.supported:
            cgroup.write(layout, entry.slice, entry.budget, found.period)
        landings.append(
            Landing(entry.slice, entry.budget, _landed(layout, entry))
        )
    state = _verdict(found.total, share, layout, landings)
    kind = layout.kind if layout is not None else None
    return Report(state, found.total, share, found.period, kind, tuple(landings))


def _landed(layout: Layout | None, entry: Entry) -> bool:
    if layout is None or not layout.supported:
        return False
    return cgroup.read(layout, entry.slice) == entry.budget


def _verdict(
    total: int, share: int, layout: Layout | None, landings: list[Landing]
) -> State:
    if total <= 0:
        return State.DISABLED
    if layout is None:
        return State.NO_CGROUP_V1
    if not layout.supported:
        return State.NO_CPU if not layout.routed else State.UNACCOUNTED
    if share <= 0:
        return State.FAILED
    applied = [item.applied for item in landings]
    if applied and all(applied):
        return State.OK
    if any(applied):
        return State.PARTIAL
    return State.FAILED
