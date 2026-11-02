# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 十个 Agent 追踪事件的发现
#
# 事件名与内核 include/trace/events/airy.h 的 TRACE_EVENT 逐字对应，
# 顺序按 KRN-01 的取证顺序排列。可读性只认 tracefs 里的实际节点：
# id 文件给出事件号，enable 文件给出当前启用位。事件目录存在但 id 缺失
# 或非整数，计为 malformed 而不是 readable，避免下游把半注册状态当成
# 可用通道。
#
# 设计依据：docs-linux/90-observability/08-agent-tracing.md §2.2。

"""AirymaxOS tracepoint discovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import files

__all__ = ["EVENTS", "Event", "discover"]

EVENTS = (
    "airy_agent_state_change",
    "airy_sched_switch",
    "airy_sched_latency",
    "airy_ipc_send",
    "airy_ipc_recv",
    "airy_superv_enforce",
    "airy_ring_buffer_write",
    "airy_lockdown_change",
    "airy_lockdown_reject",
    "airy_policy_decision",
)

READABLE = "readable"

UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class Event:
    """一个 tracepoint 的可读性与当前启用位。"""

    name: str
    id: int | None
    enabled: bool | None
    state: str
    reason: str | None

    @property
    def okay(self) -> bool:
        return self.state == READABLE


def discover(root: Path) -> tuple[Event, ...]:
    """在 tracefs 根下发现十个事件，逐个给出可读性结论。"""
    base = Path(root) / "events" / "airy"
    return tuple(_event(base, name) for name in EVENTS)


def _event(base: Path, name: str) -> Event:
    node = base / name
    if not node.is_dir():
        return Event(name, None, None, UNAVAILABLE, "missing")
    ident = files.num(node / "id")
    if ident is None:
        return Event(name, None, None, UNAVAILABLE, "malformed")
    enabled = files.text(node / "enable")
    return Event(name, ident, enabled == "1", READABLE, None)