# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 事件台账模型
#
# 状态机的边只在这里定义一次，命令行、台账与复盘都从 ALLOWED 取，
# 避免三处各写一份判定后彼此漂移。时间一律存 UTC 纳秒整数，落盘
# 时渲染成带纳秒的 ISO 串，读者拿到的是可直接比对的绝对时间。

"""Incident record, severity and the state machine."""

from __future__ import annotations

import calendar
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

SCHEMA = 1
NS = 1_000_000_000
TITLE_MAX = 120
NOTE_MAX = 1000

_ID = re.compile(r"^INC-\d{8}-\d{3}$")
_ISO = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})"
    r"(?:\.(\d{1,9}))?Z$"
)


class BadIncident(ValueError):
    """事件记录或输入不合法。"""


class BadInput(BadIncident):
    """调用方给的参数不合法，出口码 2。"""


class BadState(BadIncident):
    """状态机拒绝这次迁移，出口码 4。"""


class BadStore(BadIncident):
    """台账缺失或损坏，出口码 3。"""


class Severity(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"

    @classmethod
    def parse(cls, value: Any) -> "Severity":
        if isinstance(value, cls):
            return value
        if isinstance(value, bool):
            raise BadInput("severity 不接受布尔值")
        if isinstance(value, int):
            if 0 <= value < len(cls):
                return list(cls)[value]
            raise BadInput(f"severity 越界: {value}")
        if isinstance(value, str):
            text = value.strip().upper()
            for item in cls:
                if item.value == text:
                    return item
        raise BadInput(f"未知 severity: {value!r}")

    @property
    def rank(self) -> int:
        return list(type(self)).index(self)


class Category(str, Enum):
    SYSTEM = "system"
    AGENT = "agent"
    SECURITY = "security"
    NETWORK = "network"
    RESOURCE = "resource"

    @classmethod
    def parse(cls, value: Any) -> "Category":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            text = value.strip().lower()
            for item in cls:
                if item.value == text:
                    return item
        raise BadInput(f"未知 category: {value!r}")


class State(str, Enum):
    DETECTED = "DETECTED"
    TRIAGED = "TRIAGED"
    RESPONDING = "RESPONDING"
    RECOVERING = "RECOVERING"
    RESOLVED = "RESOLVED"
    POSTMORTEM = "POSTMORTEM"
    ESCALATED = "ESCALATED"
    REOPENED = "REOPENED"

    @classmethod
    def parse(cls, value: Any) -> "State":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            text = value.strip().upper()
            for item in cls:
                if item.value == text:
                    return item
        raise BadInput(f"未知状态: {value!r}")


DETECTED = State.DETECTED
TRIAGED = State.TRIAGED
RESPONDING = State.RESPONDING
RECOVERING = State.RECOVERING
RESOLVED = State.RESOLVED
POSTMORTEM = State.POSTMORTEM
ESCALATED = State.ESCALATED
REOPENED = State.REOPENED

# 设计文档第 3 节的状态机：只允许这些边，闭环外一律拒绝。
ALLOWED = {
    DETECTED: (TRIAGED,),
    TRIAGED: (RESPONDING,),
    RESPONDING: (RECOVERING, ESCALATED),
    RECOVERING: (RESOLVED, REOPENED),
    RESOLVED: (POSTMORTEM, REOPENED),
    ESCALATED: (RESPONDING, RECOVERING, RESOLVED),
    REOPENED: (TRIAGED, RESPONDING),
    POSTMORTEM: (),
}

# 进入状态时要落的阶段时间戳；REOPENED 反过来清掉 resolved_at，
# 否则统计会把一条仍在处置中的事件算成已恢复。
STAMP = {
    TRIAGED: "triaged_at",
    RESPONDING: "responded_at",
    RECOVERING: "recovered_at",
    RESOLVED: "resolved_at",
}

EVENT = {
    TRIAGED: "triaged",
    RESPONDING: "responded",
    RECOVERING: "recovered",
    RESOLVED: "resolved",
    POSTMORTEM: "postmortem",
    ESCALATED: "escalated",
    REOPENED: "reopened",
}

TERMINAL = (RESOLVED, POSTMORTEM)


def now_ns() -> int:
    return time.time_ns()


def iso(stamp: int) -> str:
    """纳秒时间戳渲染成 UTC ISO 串，小数位固定九位。"""
    if stamp < 0:
        raise BadInput(f"时间戳为负: {stamp}")
    secs, frac = divmod(stamp, NS)
    head = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(secs))
    return f"{head}.{frac:09d}Z"


def parse_iso(text: str) -> int:
    if not isinstance(text, str):
        raise BadInput(f"时间戳必须是字符串: {text!r}")
    hit = _ISO.match(text)
    if hit is None:
        raise BadInput(f"时间戳格式不合法: {text!r}")
    year, mon, day, hour, minute, sec = (int(hit.group(i))
                                         for i in range(1, 7))
    frac = int((hit.group(7) or "").ljust(9, "0") or 0)
    stamp = calendar.timegm((year, mon, day, hour, minute, sec, 0, 0, 0))
    return stamp * NS + frac


def day_key(stamp: int) -> str:
    return time.strftime("%Y%m%d", time.gmtime(stamp / NS))


def make_id(day: str, seq: int) -> str:
    if not 0 < seq < 1000:
        raise BadInput(f"当日序号越界: {seq}")
    return f"INC-{day}-{seq:03d}"


def check_id(ident: Any) -> str:
    if not isinstance(ident, str) or _ID.match(ident) is None:
        raise BadInput(f"事件编号不合法: {ident!r}")
    return ident


def text_of(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise BadInput(f"{name} 必须是字符串")
    body = value.strip()
    if not body:
        raise BadInput(f"{name} 不能为空")
    if len(body) > limit:
        raise BadInput(f"{name} 超过 {limit} 字符")
    return body


def names_of(value: Iterable[Any], name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        raise BadInput(f"{name} 必须是列表")
    out: list[str] = []
    for item in value:
        body = text_of(item, name, 64)
        if body not in out:
            out.append(body)
    return out


def ids_of(value: Iterable[Any], name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        raise BadInput(f"{name} 必须是列表")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise BadInput(f"{name} 含空编号")
        body = item.strip()
        if body not in out:
            out.append(body)
    return out


@dataclass
class Incident:
    incident_id: str
    severity: Severity
    category: Category
    title: str
    detected_at: int
    status: State = DETECTED
    triaged_at: int = 0
    responded_at: int = 0
    recovered_at: int = 0
    resolved_at: int = 0
    root_cause: str = ""
    source: str = ""
    timeline: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    affected_agents: list[int] = field(default_factory=list)
    affected_daemons: list[str] = field(default_factory=list)
    response_actions: list[dict[str, Any]] = field(default_factory=list)
    postmortem: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.incident_id = check_id(self.incident_id)
        self.severity = Severity.parse(self.severity)
        self.category = Category.parse(self.category)
        self.status = State.parse(self.status)
        self.title = text_of(self.title, "title", TITLE_MAX)
        if self.detected_at <= 0:
            raise BadInput("detected_at 缺失")
        self.root_cause = str(self.root_cause or "").strip()
        if len(self.root_cause) > NOTE_MAX:
            raise BadInput(f"root_cause 超过 {NOTE_MAX} 字符")
        self.alerts = ids_of(self.alerts, "alerts")
        self.affected_daemons = names_of(self.affected_daemons,
                                         "affected_daemons")
        self.affected_agents = self._agents()

    def _agents(self) -> list[int]:
        out: list[int] = []
        for item in self.affected_agents or ():
            if isinstance(item, bool) or not isinstance(item, int):
                raise BadInput("affected_agents 必须是整数列表")
            if item < 0:
                raise BadInput("affected_agents 不能为负")
            if item not in out:
                out.append(item)
        return sorted(out)

    def _note(self, stamp: int, event: str, actor: str,
              detail: str = "") -> None:
        row = {"ts": iso(stamp), "event": event, "actor": actor}
        if detail:
            row["detail"] = detail[:NOTE_MAX]
        self.timeline.append(row)

    def switch(self, target: Any, actor: str, stamp: int,
               detail: str = "") -> None:
        """按状态机迁移一次，非法边抛 BadState。"""
        want = State.parse(target)
        if want not in ALLOWED[self.status]:
            raise BadState(
                f"{self.incident_id}: {self.status.value} 不能迁移到"
                f" {want.value}")
        who = text_of(actor, "actor", 64)
        if want is REOPENED:
            self.resolved_at = 0
        self.status = want
        slot = STAMP.get(want)
        if slot is not None:
            setattr(self, slot, stamp)
        self._note(stamp, EVENT[want], who, detail)

    def action(self, name: str, actor: str, stamp: int,
               result: str = "success", detail: str = "") -> None:
        if result not in ("success", "failed"):
            raise BadInput(f"action 结果只能是 success/failed: {result}")
        row = {
            "action": text_of(name, "action", 64),
            "actor": text_of(actor, "actor", 64),
            "result": result,
            "ts": iso(stamp),
        }
        if detail:
            row["detail"] = text_of(detail, "detail", NOTE_MAX)
        self.response_actions.append(row)

    def span(self, stamp: int) -> int:
        return max(stamp - self.detected_at, 0)

    def to_json(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "schema": SCHEMA,
            "incident_id": self.incident_id,
            "severity": self.severity.value,
            "category": self.category.value,
            "title": self.title,
            "status": self.status.value,
            "detected_at": iso(self.detected_at),
            "timeline": list(self.timeline),
            "alerts": list(self.alerts),
            "affected_agents": list(self.affected_agents),
            "affected_daemons": list(self.affected_daemons),
            "response_actions": list(self.response_actions),
        }
        for slot in ("triaged_at", "responded_at", "recovered_at",
                     "resolved_at"):
            value = getattr(self, slot)
            if value:
                row[slot] = iso(value)
        if self.root_cause:
            row["root_cause"] = self.root_cause
        if self.source:
            row["source"] = self.source
        if self.postmortem is not None:
            row["postmortem"] = dict(self.postmortem)
        return row

    @classmethod
    def from_json(cls, doc: Mapping[str, Any]) -> "Incident":
        if not isinstance(doc, Mapping):
            raise BadInput("事件记录必须是对象")
        if doc.get("schema") != SCHEMA:
            raise BadInput(f"事件记录 schema 不支持: {doc.get('schema')}")
        stamps = {}
        for slot in ("detected_at", "triaged_at", "responded_at",
                     "recovered_at", "resolved_at"):
            raw = doc.get(slot)
            stamps[slot] = parse_iso(raw) if raw else 0
        actions = doc.get("response_actions")
        if actions is None:
            actions = []
        elif not isinstance(actions, list):
            raise BadInput("response_actions 必须是数组")
        for row in actions:
            if not isinstance(row, Mapping):
                raise BadInput("response_actions 含非对象项")
        timeline = doc.get("timeline")
        if timeline is None:
            timeline = []
        elif not isinstance(timeline, list):
            raise BadInput("timeline 必须是数组")
        for row in timeline:
            if not isinstance(row, Mapping):
                raise BadInput("timeline 含非对象项")
        for key in ("alerts", "affected_agents", "affected_daemons"):
            value = doc.get(key)
            if value is not None and not isinstance(value, list):
                raise BadInput(f"{key} 必须是数组")
        memo = doc.get("postmortem")
        if memo is not None and not isinstance(memo, Mapping):
            raise BadInput("postmortem 必须是对象")
        return cls(
            incident_id=doc.get("incident_id"),
            severity=doc.get("severity"),
            category=doc.get("category"),
            title=doc.get("title"),
            detected_at=stamps["detected_at"],
            status=doc.get("status"),
            triaged_at=stamps["triaged_at"],
            responded_at=stamps["responded_at"],
            recovered_at=stamps["recovered_at"],
            resolved_at=stamps["resolved_at"],
            root_cause=str(doc.get("root_cause") or ""),
            source=str(doc.get("source") or ""),
            timeline=[dict(row) for row in timeline],
            alerts=doc.get("alerts") or [],
            affected_agents=doc.get("affected_agents") or [],
            affected_daemons=doc.get("affected_daemons") or [],
            response_actions=[dict(row) for row in actions],
            postmortem=dict(memo) if memo is not None else None,
        )


def open_incident(ident: str, severity: Any, category: Any, title: str,
                  stamp: int, actor: str, source: str = "",
                  detail: str = "", alerts: Iterable[Any] = (),
                  agents: Iterable[Any] = (),
                  daemons: Iterable[Any] = ()) -> Incident:
    inc = Incident(
        incident_id=ident,
        severity=severity,
        category=category,
        title=title,
        detected_at=stamp,
        source=str(source or "").strip(),
        alerts=list(alerts or ()),
        affected_agents=list(agents or ()),
        affected_daemons=list(daemons or ()),
    )
    inc._note(stamp, "detected", text_of(actor, "actor", 64), detail)
    return inc
