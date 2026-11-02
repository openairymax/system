# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 一个采样窗口内的 tracepoint 计数
#
# ftrace 的开关是有状态的：tracing_on 与每个事件的 enable 都属于全局机器
# 状态，探针必须把它们原样恢复，否则一次取证会让运维手上的追踪配置变样。
# 因此先记录每个事件的原始 enable 值，再启用、清空、开窗、关闭、回写，
# 失败路径同样走恢复——记录留在机器上的状态只能是它原本的样子。
#
# 计数只认形如 `名称: 字段` 的事件头，不对正文做任何解析：trace 正文里的
# 数字属于事件载荷，混进计数会把一次调用算成多次。

"""AirymaxOS ftrace sampling."""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import files

__all__ = ["Sample", "record"]

OK = "ok"

IDLE = "idle"

UNAVAILABLE = "unavailable"

ON_FILE = "tracing_on"

TRACE_FILE = "trace"

EVENTS_DIR = "events/airy/{}/enable"

HEAD = re.compile(r"\b(airy_[a-z0-9_]+):")


@dataclass(frozen=True, slots=True)
class Sample:
    """一个窗口的采样结果；counts 覆盖全部被要求的事件名。"""

    window: float
    counts: tuple[tuple[str, int], ...]
    state: str
    reason: str | None

    @property
    def hit(self) -> int:
        return sum(count for _, count in self.counts)

    def count(self, name: str) -> int:
        return dict(self.counts).get(name, 0)


def record(root: Path, window: float, names: Sequence[str]) -> Sample:
    """在 tracefs 根下开一个窗口，返回窗口内各事件的出现次数。"""
    trace = Path(root)
    if not trace.is_dir():
        return Sample(window, (), UNAVAILABLE, "missing")
    prev, on, error = _arm(trace, names)
    if error is not None:
        _restore(trace, prev, on)
        return Sample(window, (), UNAVAILABLE, error)
    time.sleep(window)
    body = files.read(trace / TRACE_FILE)
    _restore(trace, prev, on)
    if body is None:
        return Sample(window, (), UNAVAILABLE, "unreadable")
    counts = _counts(body, names)
    state = OK if any(c for _, c in counts) else IDLE
    return Sample(window, counts, state, None)


def _arm(
    trace: Path, names: Sequence[str]
) -> tuple[dict[str, str], str, str | None]:
    """启用事件并清空缓冲；返回原始 enable 值、原 tracing_on 与首个失败原因。"""
    on = files.text(trace / ON_FILE) or "0"
    prev: dict[str, str] = {}
    for name in names:
        node = trace / EVENTS_DIR.format(name)
        prev[name] = files.text(node) or "0"
        error = files.write(node, "1")
        if error is not None:
            return prev, on, error
    error = files.clear(trace / TRACE_FILE)
    if error is not None:
        return prev, on, error
    return prev, on, files.write(trace / ON_FILE, "1")


def _restore(trace: Path, prev: dict[str, str], on: str) -> None:
    """关窗并把每个事件回写到采样前的 enable 值。"""
    files.write(trace / ON_FILE, on)
    for name, value in prev.items():
        files.write(trace / EVENTS_DIR.format(name), value)


def _counts(body: str, names: Sequence[str]) -> tuple[tuple[str, int], ...]:
    seen = dict.fromkeys(names, 0)
    for match in HEAD.finditer(body):
        name = match.group(1)
        if name in seen:
            seen[name] += 1
    return tuple(seen.items())