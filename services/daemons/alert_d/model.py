# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 告警数据契约
#
# 级别、来源、事件与活动告警四类结构，以及窗口与上限常量。解析入口一律
# 做类型与范围校验，越界即抛 BadEvent，不做静默截断；只有消息按 UTF-8
# 边界收敛到上限，避免日志里出现半个字符。

"""Alert records shared by the daemon, the CLI and the gate."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Any, Mapping

from airylib.err import need

NS = 1_000_000_000
DEDUP_NS = 100 * 1_000_000
AUTO_WARN_NS = 5 * 60 * NS
AUTO_CRIT_NS = 15 * 60 * NS
ESCALATE_NS = 30 * 60 * NS
STORM_QUIET_NS = 5 * 60 * NS
STORM_GAP_NS = 5 * 60 * NS
STORM_1S = 50
STORM_1M = 1000
STORM_CODE = 20
MSG_MAX = 256
STORM_MSG_MAX = 64
NOTE_MAX = 128
LABEL_MAX = 16
CODE_MAX = 0xFFFF
AGENT_MAX = 0xFFFFFFFF
TS_MAX = (1 << 63) - 1

FIRING = "FIRING"
PENDING = "PENDING_RESOLVE"
ESCALATED = "ESCALATED"
RESOLVED = "RESOLVED"


class BadEvent(ValueError):
    """Incoming record violates the alert contract."""


class Severity(IntEnum):
    INFO = 0
    WARNING = 1
    CRITICAL = 2

    @classmethod
    def parse(cls, value: Any) -> "Severity":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            key = value.strip().upper()
            if key in cls.__members__:
                return cls[key]
        if isinstance(value, int) and not isinstance(value, bool):
            need(0 <= value <= cls.CRITICAL, f"bad severity: {value!r}", BadEvent)
            return cls(value)
        raise BadEvent(f"bad severity: {value!r}")

    @property
    def label(self) -> str:
        return self.name


SOURCES = {
    1: "macro_d",
    2: "sched_d",
    3: "net_d",
    4: "mem_d",
    5: "sec_d",
    6: "cogn_d",
    7: "vfs_d",
    8: "gateway_d",
    9: "config_d",
    10: "audit_d",
    11: "kernel",
    12: "systemd",
}
_SOURCE_ID = {name: sid for sid, name in SOURCES.items()}


def source_of(value: Any) -> int:
    need(not isinstance(value, bool), "source must not be a boolean", BadEvent)
    if isinstance(value, int):
        need(value in SOURCES, f"unknown source id: {value}", BadEvent)
        return value
    if isinstance(value, str):
        key = value.strip()
        if key in _SOURCE_ID:
            return _SOURCE_ID[key]
        if key.isdigit() and int(key) in SOURCES:
            return int(key)
    raise BadEvent(f"unknown source: {value!r}")


def _num(value: Any, low: int, high: int, name: str) -> int:
    need(isinstance(value, int) and not isinstance(value, bool),
         f"{name} must be an integer", BadEvent)
    need(low <= value <= high, f"{name} out of range: {value}", BadEvent)
    return value


def clip(text: str, limit: int = MSG_MAX) -> str:
    raw = text.encode("utf-8", "replace")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", "ignore")


def parse_labels(value: Any) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    need(isinstance(value, Mapping), "labels must be an object", BadEvent)
    need(len(value) <= LABEL_MAX, f"too many labels: {len(value)}", BadEvent)
    pairs = []
    for key, val in value.items():
        need(isinstance(key, str) and isinstance(val, str),
             "labels must map strings to strings", BadEvent)
        need(key and len(key.encode("utf-8")) <= 32,
             f"bad label key: {key!r}", BadEvent)
        need(len(val.encode("utf-8")) <= 64,
             f"label value too long: {key}", BadEvent)
        pairs.append((key, val))
    return tuple(sorted(pairs))


def dedup_key(
    source: int, code: int, agent: int,
    labels: tuple[tuple[str, str], ...],
) -> str:
    tail = ",".join(f"{key}={val}" for key, val in labels)
    return f"{source}:{code}:{agent}:{tail}"


@dataclass(frozen=True)
class AlertEvent:
    ts_ns: int
    severity: Severity
    source: int
    code: int
    agent: int = 0
    message: str = ""
    labels: tuple[tuple[str, str], ...] = ()

    @classmethod
    def build(cls, obj: Any, now: int = 0) -> "AlertEvent":
        need(isinstance(obj, Mapping), "event must be an object", BadEvent)
        stamp = obj.get("ts_ns", now)
        if stamp is None:
            stamp = now
        message = obj.get("message", "")
        need(isinstance(message, str), "message must be a string", BadEvent)
        return cls(
            ts_ns=_num(stamp, 0, TS_MAX, "ts_ns"),
            severity=Severity.parse(obj.get("severity")),
            source=source_of(obj.get("source", 1)),
            code=_num(obj.get("code", 0), 0, CODE_MAX, "code"),
            agent=_num(obj.get("agent", 0), 0, AGENT_MAX, "agent"),
            message=clip(message),
            labels=parse_labels(obj.get("labels")),
        )

    @property
    def key(self) -> str:
        return dedup_key(self.source, self.code, self.agent, self.labels)

    def to_json(self) -> dict[str, Any]:
        return {
            "ts_ns": self.ts_ns,
            "severity": self.severity.label,
            "source": SOURCES[self.source],
            "code": self.code,
            "agent": self.agent,
            "message": self.message,
            "labels": dict(self.labels),
        }


@dataclass
class Alert:
    alert_id: str
    event: AlertEvent
    state: str = FIRING
    first_ns: int = 0
    last_ns: int = 0
    count: int = 1
    deadline_ns: int = 0
    next_ns: int = 0
    ack_by: str = ""
    ack_note: str = ""
    resolved_ns: int = 0

    def to_json(self) -> dict[str, Any]:
        row = self.event.to_json()
        row.update({
            "alert_id": self.alert_id,
            "state": self.state,
            "first_ns": self.first_ns,
            "last_ns": self.last_ns,
            "count": self.count,
            "deadline_ns": self.deadline_ns,
            "next_ns": self.next_ns,
            "ack_by": self.ack_by,
            "ack_note": self.ack_note,
            "resolved_ns": self.resolved_ns,
        })
        return row


def make_alert(event: AlertEvent, now: int) -> Alert:
    seed = f"{event.key}|{now}".encode("utf-8")
    ident = hashlib.blake2s(seed, digest_size=8).hexdigest()
    return Alert(alert_id=ident, event=event, first_ns=now, last_ns=now)


def raise_sev(alert: Alert, severity: Severity) -> None:
    if severity > alert.event.severity:
        alert.event = replace(alert.event, severity=severity)
