# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 追踪取证服务包
#
# 内核已经把 Agent 调度、IPC、监督、环形缓冲、锁定与策略裁决六类动作导出
# 成十个 tracepoint，也在 securityfs、procfs 与 debugfs 上留下了三处静态
# 计数。缺的从来不是可观测性本身，而是一份能复算的取证结论：哪条通道在
# 这台机器上可读、为什么不可读、同一动作重复多次时计数是否真的动了。
# 本包把「发现、采样、判定」收在一处，命令行与验收门禁共用同一份实现。
#
# 本版不新增内核注入接口（见 0.0.4 方案 §2.3），注入只经既有只读通道
# 试探并把 errno 原样留证。
#
# 设计依据：docs-linux/90-observability/08-agent-tracing.md §2.2，
# docs-linux/90-observability/01-ftrace-framework.md §2。

"""AirymaxOS probe service package (X-02)."""

from __future__ import annotations

from .channels import Channel, Outcome, attempt, inspect
from .events import EVENTS, Event, discover
from .paths import Paths, defaults
from .probe import sample, status, verify
from .report import Report, State
from .stats import Stats, Table, snapshot
from .trace import Sample, record

__all__ = [
    "EVENTS",
    "Channel",
    "Event",
    "Outcome",
    "Paths",
    "Report",
    "Sample",
    "State",
    "Stats",
    "Table",
    "attempt",
    "defaults",
    "discover",
    "inspect",
    "record",
    "sample",
    "snapshot",
    "status",
    "verify",
]