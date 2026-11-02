# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 探针的判定结论
#
# 验收要回答的不是「探针跑没跑」，而是「这台机器上十条追踪通道现在是什么
# 状态」。三种结论对应三种处置：blocked 是没有 tracefs 或没有 airy 事件，
# 说明本机内核不具备取证面，必须换机器或换内核；partial 是取证面部分可用，
# 缺哪条、为什么缺都要能直接读到；ok 是十条全部可读、三处静态计数全部
# 读到、采样窗口没有失败。
#
# verify 模式比 status 多一条判据：同一动作重复多次时计数必须单调递增。
# 没有观测到递增就不是「验证通过」，而是「窗口内无事件」，结论落 partial，
# 由操作者带着业务负载重跑，而不是把空窗口当成功。

"""AirymaxOS probe verdict."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

from .channels import Channel, Outcome
from .events import Event
from .stats import Stats, Table
from .trace import UNAVAILABLE, Sample

__all__ = ["STATUS", "SAMPLE", "VERIFY", "Report", "State"]

NAME = "airy-probe"

VERSION = "0.0.4"

STATUS = "status"

SAMPLE = "sample"

VERIFY = "verify"

OK_HINT = "取证面完整，十点追踪与三处计数均可用"

SURFACE_HINT = "取证面部分可用，按 missing 与 stats 逐项补齐"

WINDOW_HINT = "采样窗口失败，按 samples 的 reason 排查节点"

IDLE_HINT = "十条通道可读但窗口内无事件，带业务负载重跑"

BLOCKED_HINT = "本机没有 airy 取证面，换 AirymaxOS 内核或换机器"


@unique
class State(Enum):
    """一次探测的结论；只有 ``OK`` 表示十条通道与三处计数全部可用。"""

    OK = "ok"
    PARTIAL = "partial"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class Report:
    """一次 status / sample / verify 的完整结论。"""

    mode: str
    events: tuple[Event, ...]
    channels: tuple[Channel, ...]
    attempts: tuple[Outcome, ...]
    stats: Stats
    samples: tuple[Sample, ...]

    @property
    def readable(self) -> int:
        return sum(1 for item in self.events if item.okay)

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.events if not item.okay)

    @property
    def witnessed(self) -> bool:
        """多次采样中是否出现过严格递增的事件计数。"""
        if len(self.samples) < 2:
            return False
        return any(self._rising(item.name) for item in self.events)

    @property
    def state(self) -> State:
        if self.readable == 0:
            return State.BLOCKED
        if self.readable < len(self.events) or not self.stats.facts:
            return State.PARTIAL
        if any(item.state == UNAVAILABLE for item in self.samples):
            return State.PARTIAL
        if self.mode == VERIFY and not self.witnessed:
            return State.PARTIAL
        return State.OK

    @property
    def hint(self) -> str:
        """结论的处置建议；按首个成立的原因给出，不按状态笼统归类。"""
        if self.readable == 0:
            return BLOCKED_HINT
        if self.missing or not self.stats.facts:
            return SURFACE_HINT
        if any(item.state == UNAVAILABLE for item in self.samples):
            return WINDOW_HINT
        if self.mode == VERIFY and not self.witnessed:
            return IDLE_HINT
        return OK_HINT

    def _rising(self, name: str) -> bool:
        series = [item.count(name) for item in self.samples]
        return any(later > prev for prev, later in zip(series, series[1:]))

    def dump(self) -> dict[str, object]:
        """命令行 --json 的唯一输出形状。"""
        return {
            "probe": NAME,
            "version": VERSION,
            "mode": self.mode,
            "state": self.state.value,
            "hint": self.hint,
            "readable": self.readable,
            "total": len(self.events),
            "events": [_event(item) for item in self.events],
            "missing": list(self.missing),
            "channels": [_chan(item) for item in self.channels],
            "attempts": [_outcome(item) for item in self.attempts],
            "stats": _stats(self.stats),
            "samples": [_sample(item) for item in self.samples],
            "witnessed": self.witnessed,
        }


def _event(item: Event) -> dict[str, object]:
    return {
        "name": item.name,
        "id": item.id,
        "enabled": item.enabled,
        "state": item.state,
        "reason": item.reason,
    }


def _chan(item: Channel) -> dict[str, object]:
    return {
        "name": item.name,
        "path": item.path,
        "mode": item.mode,
        "present": item.present,
        "writable": item.writable,
        "reason": item.reason,
    }


def _outcome(item: Outcome) -> dict[str, object]:
    return {
        "channel": item.channel,
        "path": item.path,
        "error": item.error,
        "delivered": item.delivered,
    }


def _table(item: Table) -> dict[str, object]:
    return {
        "name": item.name,
        "hook": item.hook,
        "rows": item.rows,
        "evals": item.evals,
        "allow": item.allow,
        "deny": item.deny,
        "audit": item.audit,
        "complain": item.complain,
        "fault": item.fault,
    }


def _stats(item: Stats) -> dict[str, object]:
    return {
        "cap_used": item.cap_used,
        "stc_total": item.stc_total,
        "stc_policies": dict(item.stc_policies),
        "policy": item.policy,
        "policy_reason": item.policy_reason,
        "tables": [_table(row) for row in item.tables],
    }


def _sample(item: Sample) -> dict[str, object]:
    return {
        "window": item.window,
        "state": item.state,
        "reason": item.reason,
        "hit": item.hit,
        "counts": dict(item.counts),
    }