# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS Agent 生命周期模型
#
# 8 个状态与合法迁移边只在这里定义一次：命令行、健康评估与资源调优都从
# ALLOWED 取判定，避免三处各写一份后彼此漂移。非法迁移一律抛出 BadState，
# 由命令行映射为退出码 3，不做静默纠正。
#
# 范围边界：跨节点迁移与滚动升级（设计 §9.3）本版不实现；相关子命令只返回
# 固定错误「本版单节点不提供」，不伪造迁移结果。

"""Agent record, state machine and input parsing."""

from __future__ import annotations

import calendar
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from airylib.err import need

SCHEMA = 1
NS = 1_000_000_000
HISTORY_MAX = 200
TOKEN_RESET = "manual"

_ID = re.compile(r"^[0-9]{1,10}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CAP = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_MB = re.compile(r"^([0-9]+)\s*(MB|M|GB|G)?$", re.IGNORECASE)
_MS = re.compile(r"^([0-9]+)\s*(MS|S)?$", re.IGNORECASE)
_ISO = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})"
    r"(?:\.(\d{1,9}))?Z$"
)


class BadAgent(ValueError):
    """Agent 运维可预期的失败基类。"""


class BadInput(BadAgent):
    """调用方参数不合法，出口码 2。"""


class BadState(BadAgent):
    """状态机拒绝这次迁移，出口码 3。"""


class BadStore(BadAgent):
    """元数据缺失或损坏，出口码 3。"""


class BadConfig(BadAgent):
    """配置文件不合法，出口码 3。"""


class BadPerm(BadAgent):
    """调用方不在 airy-log 组，出口码 4。"""


class BadUnsupported(BadState):
    """本版单节点不提供的能力，出口码 3。"""


class State(str, Enum):
    INACTIVE = "INACTIVE"
    SPAWNING = "SPAWNING"
    READY = "READY"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    DEAD = "DEAD"


ALLOWED: dict[State, frozenset[State]] = {
    State.INACTIVE: frozenset({State.SPAWNING, State.DEAD}),
    State.SPAWNING: frozenset({State.READY, State.STOPPING, State.DEAD}),
    State.READY: frozenset(
        {State.RUNNING, State.BLOCKED, State.STOPPING}),
    State.RUNNING: frozenset({State.BLOCKED, State.STOPPING}),
    State.BLOCKED: frozenset({State.RUNNING, State.STOPPING}),
    State.STOPPING: frozenset({State.STOPPED, State.DEAD}),
    State.STOPPED: frozenset(
        {State.SPAWNING, State.READY, State.RUNNING, State.DEAD}),
    State.DEAD: frozenset(),
}


def now_ns() -> int:
    return time.time_ns()


def iso(stamp: int) -> str:
    sec, frac = divmod(int(stamp), NS)
    text = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(sec))
    return f"{text}.{frac:09d}Z"


def parse_iso(text: str) -> int:
    need(isinstance(text, str), f"时间戳不是字符串: {text!r}", BadInput)
    match = _ISO.match(text.strip())
    need(match is not None, f"时间戳格式不合法: {text!r}", BadInput)
    parts = [int(item or 0) for item in match.groups()]
    year, month, day, hour, minute, second, frac = parts
    sec = calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0))
    return sec * NS + frac


def check_id(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    need(_ID.match(text) is not None, f"Agent ID 必须是正整数: {value!r}",
         BadInput)
    number = int(text)
    need(number > 0, f"Agent ID 必须大于 0: {value!r}", BadInput)
    return str(number)


def check_name(value: Any, field_name: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    need(_NAME.match(text) is not None,
         f"{field_name} 只允许字母数字与 ._-，长度 1-64: {value!r}",
         BadInput)
    return text


def parse_mb(value: Any) -> int:
    match = _MB.match(str(value).strip()) if value is not None else None
    need(match is not None, f"内存容量格式不合法: {value!r}", BadInput)
    number = int(match.group(1))
    unit = (match.group(2) or "MB").upper()
    total = number * 1024 if unit in ("GB", "G") else number
    need(total > 0, f"内存容量必须大于 0: {value!r}", BadInput)
    return total


def parse_ms(value: Any) -> int:
    match = _MS.match(str(value).strip()) if value is not None else None
    need(match is not None, f"时间参数格式不合法: {value!r}", BadInput)
    number = int(match.group(1))
    unit = (match.group(2) or "MS").upper()
    total = number * 1000 if unit == "S" else number
    need(total > 0, f"时间参数必须大于 0: {value!r}", BadInput)
    return total


def split_caps(value: Any) -> list[str]:
    text = value.strip() if isinstance(value, str) else ""
    need(bool(text), "capabilities 不能为空", BadInput)
    out: list[str] = []
    for item in text.split(","):
        name = item.strip()
        need(_CAP.match(name) is not None,
             f"能力名不合法: {item!r}", BadInput)
        if name not in out:
            out.append(name)
    return out


def need_sched(sched: Mapping[str, Any]) -> None:
    need(isinstance(sched, Mapping), "调度参数必须是映射", BadInput)
    values = []
    for key in ("runtime_ms", "deadline_ms", "period_ms"):
        item = sched.get(key)
        need(isinstance(item, int) and not isinstance(item, bool) and item > 0,
             f"调度参数 {key} 必须是正整数", BadInput)
        values.append(item)
    runtime, deadline, period = values
    need(runtime <= deadline <= period,
         "调度参数必须满足 runtime <= deadline <= period", BadInput)


def need_mem(memory: Mapping[str, Any]) -> None:
    need(isinstance(memory, Mapping), "内存参数必须是映射", BadInput)
    l1, l2 = memory.get("l1_mb"), memory.get("l2_mb")
    for name, item in (("l1_mb", l1), ("l2_mb", l2)):
        need(isinstance(item, int) and not isinstance(item, bool) and item > 0,
             f"内存参数 {name} 必须是正整数", BadInput)
    need(l1 <= l2, "内存参数必须满足 l1_mb <= l2_mb", BadInput)


def _need_token(token: Mapping[str, Any]) -> None:
    need(isinstance(token, Mapping), "令牌参数必须是映射", BadInput)
    total, used = token.get("total_budget"), token.get("consumed")
    for name, item in (("total_budget", total), ("consumed", used)):
        need(isinstance(item, int) and not isinstance(item, bool) and item >= 0,
             f"令牌参数 {name} 必须是非负整数", BadInput)
    need(total > 0, "令牌总额必须大于 0", BadInput)


@dataclass
class Agent:
    id: str
    name: str
    template: str
    state: State
    created_at: int
    updated_at: int
    token: dict[str, Any]
    memory: dict[str, int]
    sched: dict[str, int]
    capabilities: list[str] = field(default_factory=list)
    auto_tune: bool = False
    pid: int | None = None
    heartbeat_timeouts: int | None = None
    ipc_p99_ms: float | None = None
    transitions: list[dict[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.id = check_id(self.id)
        self.name = check_name(self.name, "name")
        self.template = check_name(self.template, "template")
        self.state = State(self.state)
        need_sched(self.sched)
        need_mem(self.memory)
        _need_token(self.token)
        need(self.pid is None or (isinstance(self.pid, int) and self.pid > 0),
             "pid 必须是正整数或 null", BadInput)
        need(self.heartbeat_timeouts is None
             or (isinstance(self.heartbeat_timeouts, int)
                 and self.heartbeat_timeouts >= 0),
             "heartbeat_timeouts 必须是非负整数或 null", BadInput)
        need(self.ipc_p99_ms is None
             or (isinstance(self.ipc_p99_ms, (int, float))
                 and self.ipc_p99_ms >= 0),
             "ipc_p99_ms 必须是非负数或 null", BadInput)

    def switch(self, target: State, action: str, stamp: int = 0) -> None:
        target = State(target)
        need(target in ALLOWED[self.state],
             f"Agent {self.id}: {self.state.value} 不能转到 {target.value}"
             f"（{action}）", BadState)
        at = stamp or now_ns()
        self._note(target, action, at)
        self.state = target
        self.updated_at = at

    def audit(self, action: str, stamp: int = 0) -> None:
        at = stamp or now_ns()
        self._note(self.state, action, at)
        self.updated_at = at

    def _note(self, target: State, action: str, stamp: int) -> None:
        self.transitions.append({
            "at": iso(stamp), "from": self.state.value,
            "to": State(target).value, "action": action,
        })
        if len(self.transitions) > HISTORY_MAX:
            del self.transitions[:-HISTORY_MAX]

    def used_pct(self) -> float:
        total = self.token["total_budget"]
        return round(min(100.0, self.token["consumed"] * 100.0 / total), 3)

    def remaining(self) -> int:
        return max(self.token["total_budget"] - self.token["consumed"], 0)

    def rate(self, stamp: int = 0) -> float:
        reset = self.token.get("last_reset_at")
        base = parse_iso(reset) if reset else self.created_at
        seconds = max(((stamp or now_ns()) - base) / NS, 1.0)
        return round(self.token["consumed"] / seconds, 3)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "id": self.id,
            "name": self.name,
            "template": self.template,
            "state": self.state.value,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
            "token": dict(self.token),
            "memory": dict(self.memory),
            "sched": dict(self.sched),
            "capabilities": list(self.capabilities),
            "auto_tune": bool(self.auto_tune),
            "pid": self.pid,
            "heartbeat_timeouts": self.heartbeat_timeouts,
            "ipc_p99_ms": self.ipc_p99_ms,
            "transitions": list(self.transitions),
        }

    @classmethod
    def from_json(cls, doc: Any) -> "Agent":
        need(isinstance(doc, dict), "meta.json 顶层不是对象", BadStore)
        need(doc.get("schema") == SCHEMA, "meta.json schema 不受支持", BadStore)
        try:
            return cls(
                id=check_id(doc.get("id")),
                name=check_name(doc.get("name"), "name"),
                template=check_name(doc.get("template"), "template"),
                state=State(doc.get("state")),
                created_at=parse_iso(doc.get("created_at")),
                updated_at=parse_iso(doc.get("updated_at")),
                token=dict(doc.get("token") or {}),
                memory=dict(doc.get("memory") or {}),
                sched=dict(doc.get("sched") or {}),
                capabilities=list(doc.get("capabilities") or []),
                auto_tune=bool(doc.get("auto_tune")),
                pid=doc.get("pid"),
                heartbeat_timeouts=doc.get("heartbeat_timeouts"),
                ipc_p99_ms=doc.get("ipc_p99_ms"),
                transitions=list(doc.get("transitions") or []),
            )
        except (TypeError, KeyError) as exc:
            raise BadStore(f"meta.json 字段缺失: {exc}") from exc
