# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 实时带宽的判定结论
#
# 验收侧要回答的不是「写没写」，而是「现在能不能跑实时」。同一个「失败」
# 背后有五种完全不同的处置：内核不限预算（disabled，无需处理）、没有
# cgroup 记账树（no-cgroup-v1，装机缺陷）、统一树没有 cpu 控制器
# （no-cpu，内核配置缺陷）、有控制器但不记账（unaccounted）、以及该写
# 没写进去（partial/failed，权限或层级约束）。把它们压成一个布尔值，
# 运维只能看到「实时不可用」，排查要从零开始。
#
# unaccounted 不是缺陷：0.0.4 的内核关闭了 CONFIG_RT_GROUP_SCHED，实时
# 任务统一受全局预算 kernel.sched_rt_runtime_us 限流，没有可写的分组
# 预算文件。此时「没有落地份额」正是内核契约，验收按放行处理；只有
# partial/failed 才说明该落的预算没落下去。把它判成失败会让正常装机
# 在开机阶段降级，2026-11-05 的 0.0.4 装机失败即由此而来。
#
# token 与中文建议分开：token 供脚本比对，建议由命令行渲染，二者不混在
# 数据结构里，避免脚本去解析人类可读文本。

"""AirymaxOS RT bandwidth verdict (X-01)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

from .cgroup import Kind

__all__ = ["Landing", "Report", "State"]


@unique
class State(Enum):
    """一次判定的结论；只有 ``OK`` 表示预算已全部落地。"""

    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    DISABLED = "disabled"
    NO_CGROUP_V1 = "no-cgroup-v1"
    UNACCOUNTED = "unaccounted"
    NO_CPU = "no-cpu"


@dataclass(frozen=True, slots=True)
class Landing:
    """单个 slice 的落地结果。"""

    slice: str
    budget: int
    applied: bool


@dataclass(frozen=True, slots=True)
class Report:
    """一次 inspect 或 apply 的完整结论。"""

    state: State
    total: int
    share: int
    period: int
    kind: Kind | None
    landings: tuple[Landing, ...]

    @property
    def ok(self) -> bool:
        return self.state is State.OK

    @property
    def passing(self) -> bool:
        """内核契约已满足：预算落地、内核不限，或按全局预算统一限流。"""
        return self.state in (State.OK, State.DISABLED, State.UNACCOUNTED)

    @property
    def wanted(self) -> int:
        """本次计划覆盖的 slice 数；预算不可切分时为 0。"""
        return len(self.landings)

    @property
    def applied(self) -> int:
        return sum(1 for item in self.landings if item.applied)

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(item.slice for item in self.landings if not item.applied)

    def dump(self) -> dict[str, object]:
        """命令行 --json 的唯一输出形状。"""
        return {
            "state": self.state.value,
            "total": self.total,
            "share": self.share,
            "period": self.period,
            "kind": self.kind.value if self.kind else "",
            "wanted": self.wanted,
            "applied": self.applied,
            "missing": list(self.missing),
            "slices": [
                {
                    "slice": item.slice,
                    "budget": item.budget,
                    "applied": item.applied,
                }
                for item in self.landings
            ],
        }
