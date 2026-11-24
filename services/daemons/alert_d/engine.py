# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 告警处理引擎
#
# 去重、抑制、风暴与生命周期四步在同一处完成，顺序与设计文档一致：先去重
# （同源同码同 Agent 同标签 100ms 合并），再风暴闸门（WARNING/INFO 每键
# 5 分钟放行一条），再规则抑制，最后创建或刷新活动告警。时间由调用方以
# 单调纳秒传入，引擎自身不读时钟，因此每一条判据都能在测试里复现。

"""Deterministic alert pipeline."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from typing import Any, Iterable

from .model import (
    AUTO_CRIT_NS, AUTO_WARN_NS, DEDUP_NS, ESCALATE_NS, FIRING, PENDING,
    RESOLVED, ESCALATED, STORM_1M, STORM_1S, STORM_CODE, STORM_GAP_NS,
    STORM_MSG_MAX, STORM_QUIET_NS, Alert, AlertEvent, Severity, clip,
    make_alert, raise_sev,
)
from .rules import Rule

NS = 1_000_000_000


class Action:
    """One observable outcome of the pipeline."""

    __slots__ = ("kind", "alert", "note")

    def __init__(self, kind: str, alert: Alert | None = None, note: str = ""):
        self.kind = kind
        self.alert = alert
        self.note = note

    def to_json(self) -> dict[str, Any]:
        row: dict[str, Any] = {"action": self.kind}
        if self.alert is not None:
            row["alert"] = self.alert.to_json()
        if self.note:
            row["note"] = self.note
        return row


class Engine:
    """In-memory alert state machine."""

    def __init__(self, rules: Iterable[Rule] = ()):
        self.rules: tuple[Rule, ...] = tuple(rules)
        self.active: dict[str, Alert] = {}
        self.ledger: dict[str, list[int]] = {}
        self.rate: deque[tuple[int, int]] = deque()
        self.gap: dict[str, int] = {}
        self.storm = False
        self.hot_ns = 0
        self.counts = {
            "received": 0,
            "emitted": 0,
            "deduped": 0,
            "suppressed": 0,
            "escalated": 0,
            "resolved": 0,
            "storms": 0,
        }

    def reload(self, rules: Iterable[Rule]) -> None:
        self.rules = tuple(rules)
        self.ledger.clear()

    def ingest(self, event: AlertEvent, now: int) -> list[Action]:
        self.counts["received"] += 1
        self.rate.append((now, event.code))
        self._prune(now)
        out: list[Action] = []
        storm = self._storm(now)
        if storm is not None:
            out.append(storm)

        key = event.key
        current = self.active.get(key)
        duplicate = self._duplicate(current, now)
        if duplicate is not None:
            out.append(duplicate)
            return out

        blocked = self._block(self._calm(event), key, now)
        if blocked is not None:
            self._count_hit(current, now)
            self.counts["suppressed"] += 1
            out.append(Action("SUPPRESS", current, blocked))
            return out

        out.append(self._advance(self._calm(event), current, key, now))
        self.counts["emitted"] += 1
        if self.storm:
            self.gap[key] = now
        return out

    def _duplicate(self, current: Alert | None, now: int) -> Action | None:
        if current is None or now - current.last_ns >= DEDUP_NS:
            return None
        current.count += 1
        current.last_ns = now
        self.counts["deduped"] += 1
        return Action("DEDUP", current, "within dedup window")

    def _calm(self, event: AlertEvent) -> AlertEvent:
        # 风暴期只让 CRITICAL 透出，且消息截短，避免刷屏占满归档。
        if self.storm and event.severity == Severity.CRITICAL:
            return replace(event, message=clip(event.message, STORM_MSG_MAX))
        return event

    def _block(self, event: AlertEvent, key: str, now: int) -> str | None:
        # 风暴闸门优先于规则抑制：闸门拦下的条目不该消耗规则配额。
        if self.storm and not self._gap_ok(key, now):
            return "storm"
        rule = self._rule_hit(event, now)
        return None if rule is None else rule.name

    def _advance(self, event: AlertEvent, current: Alert | None, key: str,
                 now: int) -> Action:
        if current is None:
            alert = make_alert(event, now)
            self.active[key] = alert
            return Action("EMIT", alert, "")
        current.count += 1
        current.last_ns = now
        raise_sev(current, event.severity)
        if current.state != PENDING:
            return Action("NOTIFY", current, "alert refreshed")
        current.state = FIRING
        current.deadline_ns = 0
        return Action("REFIRE", current, "recurrence before confirmation")

    def recover(self, event: AlertEvent, now: int) -> list[Action]:
        current = self.active.get(event.key)
        if current is None:
            return [Action("IGNORE", None, "recovery without active alert")]
        if current.state == PENDING:
            return [Action("DEDUP", current, "already pending resolve")]
        current.state = PENDING
        current.last_ns = now
        span = AUTO_CRIT_NS if current.event.severity == Severity.CRITICAL else AUTO_WARN_NS
        current.deadline_ns = now + span
        return [Action("PENDING", current, "waiting for confirmation")]

    def ack(self, alert_id: str, operator: str, note: str, now: int) -> list[Action]:
        for key, alert in list(self.active.items()):
            if alert.alert_id != alert_id:
                continue
            alert.state = RESOLVED
            alert.resolved_ns = now
            alert.ack_by = clip(operator, 64)
            alert.ack_note = clip(note, 96)
            self.counts["resolved"] += 1
            del self.active[key]
            return [Action("ACK", alert, "operator confirmed")]
        return [Action("IGNORE", None, "unknown alert id")]

    def tick(self, now: int) -> list[Action]:
        out: list[Action] = []
        for key, alert in list(self.active.items()):
            sev = alert.event.severity
            if alert.state == FIRING and sev == Severity.CRITICAL and \
                    now - alert.first_ns >= ESCALATE_NS:
                alert.state = ESCALATED
                alert.next_ns = now + ESCALATE_NS
                self.counts["escalated"] += 1
                out.append(Action("ESCALATE", alert, "critical alert kept firing"))
            elif alert.state == ESCALATED and now >= alert.next_ns:
                alert.next_ns = now + ESCALATE_NS
                self.counts["escalated"] += 1
                out.append(Action("ESCALATE", alert, "repeat escalation"))
            elif alert.state == PENDING and now >= alert.deadline_ns:
                alert.state = RESOLVED
                alert.resolved_ns = now
                self.counts["resolved"] += 1
                out.append(Action("RESOLVE", alert, "auto confirmed"))
                del self.active[key]
        storm = self._storm(now)
        if storm is not None:
            out.append(storm)
        return out

    def status(self, now: int) -> dict[str, Any]:
        return {
            "schema": 1,
            "storm": self.storm,
            "active": len(self.active),
            "counts": dict(self.counts),
            "updated_ns": now,
        }

    def _count_hit(self, alert: Alert | None, now: int) -> None:
        if alert is not None:
            alert.count += 1
            alert.last_ns = now

    def _prune(self, now: int) -> None:
        edge = now - 60 * NS
        while self.rate and self.rate[0][0] < edge:
            self.rate.popleft()

    def _hot(self, now: int) -> bool:
        edge = now - 60 * NS
        recent = 0
        per_code: dict[int, int] = {}
        for stamp, code in self.rate:
            if stamp < edge:
                continue
            if now - stamp <= NS:
                recent += 1
            per_code[code] = per_code.get(code, 0) + 1
        if not per_code:
            return False
        if recent > STORM_1S or sum(per_code.values()) > STORM_1M:
            return True
        return max(per_code.values()) > STORM_CODE

    def _storm(self, now: int) -> Action | None:
        hot = self._hot(now)
        if self.storm:
            if hot:
                self.hot_ns = now
                return None
            if now - self.hot_ns < STORM_QUIET_NS:
                return None
            self.storm = False
            return Action("STORM_EXIT", None, "rate back below threshold")
        if not hot:
            return None
        self.storm = True
        self.hot_ns = now
        self.counts["storms"] += 1
        return Action("STORM_ENTER", None, "rate above storm threshold")

    def _gap_ok(self, key: str, now: int) -> bool:
        last = self.gap.get(key)
        return last is None or now - last >= STORM_GAP_NS

    def _rule_hit(self, event: AlertEvent, now: int) -> Rule | None:
        for rule in self.rules:
            if not rule.match(event):
                continue
            slot = self.ledger.get(rule.name)
            if slot is None or now - slot[0] >= rule.window_ns:
                slot = [now, 0]
                self.ledger[rule.name] = slot
            if rule.max_emit == 0 or slot[1] >= rule.max_emit:
                return rule
            slot[1] += 1
            return None
        return None
