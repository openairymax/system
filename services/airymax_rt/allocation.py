# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 带宽切分计划
#
# 计划是纯函数：只由策略与总预算算出每个 slice 该拿多少，不碰文件系统。
# 把「算」与「写」分开，验收侧才能在没有 cgroup 写权限的机器上复算同一
# 份结论，写入失败也不会污染计划本身。

"""AirymaxOS RT bandwidth allocation plan (X-01)."""

from __future__ import annotations

from dataclasses import dataclass

from .policy import Policy

__all__ = ["Entry", "allocate"]


@dataclass(frozen=True, slots=True)
class Entry:
    """一个 slice 的目标预算，单位为微秒每周期。"""

    slice: str
    budget: int


def allocate(policy: Policy, total: int) -> tuple[Entry, ...]:
    """把总预算切成每份相同的条目；每份为 0 时不产生计划。

    每份为 0 说明总预算小到无法切分（或内核不限制），此时返回空计划，
    由上层据总预算判 disabled / failed，而不是写入 0 把 RT 关掉。
    """
    share = policy.share(total)
    if share <= 0:
        return ()
    return tuple(Entry(name, share) for name in policy.slices)
