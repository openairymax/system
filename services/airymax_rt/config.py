# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 实时带宽的参考配置与判定建议
#
# 内核总预算的落盘归 airymaxos-kernel 的 sysctl 文件所有：同一条路径只能
# 有一个包声明，否则两个包互相覆盖，rpm 校验还会报同一路径被两包持有。
# 因此本包只生成参考文本供人工核对，绝不写盘。
#
# 判定 token 与中文建议分开：token 给脚本比对，建议给人看，二者不混进
# 同一个数据结构，脚本就不必去解析人类可读文本。

"""AirymaxOS RT bandwidth reference and advice (X-01)."""

from __future__ import annotations

from .report import State

__all__ = ["ADVICE", "REF_PERIOD", "REF_TOTAL", "advice", "reference"]

REF_TOTAL = 950000

REF_PERIOD = 1000000

ADVICE = {
    State.OK: "实时带宽已全部落地",
    State.PARTIAL: "部分 slice 未落地：检查写入权限与父组剩余预算",
    State.FAILED: "没有任何 slice 落地：检查权限与层级预算上限",
    State.DISABLED: "内核不限制实时带宽（-1），无需切分",
    State.NO_CGROUP_V1: "未找到 cgroup cpu 记账树：确认 cpu 控制器已挂载",
    State.UNACCOUNTED: (
        "内核按全局预算限流实时任务（未启用 RT 组调度），无需分组记账"
    ),
    State.NO_CPU: (
        "实时预算接口不可用：检查 cpu 控制器挂载与总预算文件"
    ),
}


def advice(state: State) -> str:
    """判定 token 对应的处置建议。"""
    return ADVICE[state]


def reference() -> str:
    """参考 sysctl 文本；落盘归 airymaxos-kernel，本包只打印。"""
    return (
        "# AirymaxOS 实时带宽参考（落盘归 airymaxos-kernel）\n"
        f"kernel.sched_rt_runtime_us = {REF_TOTAL}\n"
        f"kernel.sched_rt_period_us = {REF_PERIOD}\n"
    )
