# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 告警引擎测试
#
# 引擎不读时钟，所有判据都以显式纳秒推进，因此去重窗口、风暴闸门、规则
# 配额、恢复确认与升级时刻都能精确复现，不依赖等待。

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "daemons"))

from alert_d.engine import Engine  # noqa: E402
from alert_d.model import (  # noqa: E402
    AUTO_WARN_NS, DEDUP_NS, ESCALATE_NS, FIRING, PENDING, RESOLVED,
    STORM_1S, STORM_QUIET_NS, AlertEvent, Severity,
)
from alert_d.rules import Rule  # noqa: E402

NS = 1_000_000_000


def event(severity: str = "WARNING", code: int = 7, agent: int = 0,
          source: str = "macro_d", message: str = "m") -> AlertEvent:
    return AlertEvent.build({
        "ts_ns": 0, "severity": severity, "code": code,
        "agent": agent, "source": source, "message": message,
    })


def kinds(actions) -> list[str]:
    return [act.kind for act in actions]


class IngestTest(unittest.TestCase):
    def test_emit_then_notify(self) -> None:
        engine = Engine()
        first = engine.ingest(event(), 0)
        self.assertEqual(kinds(first), ["EMIT"])
        alert = first[-1].alert
        again = engine.ingest(event(), DEDUP_NS + 1)
        self.assertEqual(kinds(again), ["NOTIFY"])
        self.assertEqual(again[-1].alert.alert_id, alert.alert_id)
        self.assertEqual(alert.count, 2)
        self.assertEqual(engine.counts["received"], 2)
        self.assertEqual(engine.counts["emitted"], 2)

    def test_dedup_window_merges(self) -> None:
        engine = Engine()
        engine.ingest(event(), 0)
        out = engine.ingest(event(), DEDUP_NS - 1)
        self.assertEqual(kinds(out), ["DEDUP"])
        self.assertEqual(engine.counts["deduped"], 1)
        self.assertEqual(len(engine.active), 1)

    def test_severity_only_raised(self) -> None:
        engine = Engine()
        engine.ingest(event("WARNING"), 0)
        engine.ingest(event("INFO"), DEDUP_NS + 1)
        alert = next(iter(engine.active.values()))
        self.assertIs(alert.event.severity, Severity.WARNING)
        engine.ingest(event("CRITICAL"), 2 * DEDUP_NS + 2)
        self.assertIs(alert.event.severity, Severity.CRITICAL)


class RuleTest(unittest.TestCase):
    def test_max_emit_zero_always_suppresses(self) -> None:
        engine = Engine([Rule("mute", NS, 0, source=1)])
        out = engine.ingest(event(), 0)
        self.assertEqual(kinds(out), ["SUPPRESS"])
        self.assertEqual(out[-1].note, "mute")
        self.assertEqual(engine.active, {})

    def test_quota_then_suppress(self) -> None:
        engine = Engine([Rule("brief", 10 * NS, 1, source=1,
                              severity=Severity.INFO)])
        self.assertEqual(kinds(engine.ingest(event("INFO"), 0)), ["EMIT"])
        self.assertEqual(kinds(engine.ingest(event("INFO"), NS)), ["SUPPRESS"])
        self.assertEqual(kinds(engine.ingest(event("INFO"), 2 * NS)), ["SUPPRESS"])
        self.assertEqual(kinds(engine.ingest(event("INFO"), 11 * NS)), ["NOTIFY"])

    def test_non_matching_rule_passes(self) -> None:
        engine = Engine([Rule("net", NS, 0, source=3)])
        self.assertEqual(kinds(engine.ingest(event(), 0)), ["EMIT"])

    def test_reload_clears_ledger(self) -> None:
        rule = Rule("brief", NS, 1)
        engine = Engine([rule])
        engine.ingest(event(), 0)
        engine.reload([rule])
        self.assertEqual(engine.ledger, {})
        self.assertEqual(kinds(engine.ingest(event(), DEDUP_NS + 1)), ["NOTIFY"])


class StormTest(unittest.TestCase):
    def storm(self, engine: Engine, start: int = 0) -> int:
        now = start
        for idx in range(STORM_1S + 1):
            now = start + idx * 1_000_000
            out = engine.ingest(event(code=42, agent=idx), now)
            if "STORM_ENTER" in kinds(out):
                break
        self.assertTrue(engine.storm)
        return now

    def test_enter_then_gap(self) -> None:
        engine = Engine()
        now = self.storm(engine)
        self.assertEqual(engine.counts["storms"], 1)

        first = engine.ingest(event(code=43, agent=1000), now + DEDUP_NS + 1)
        self.assertEqual(kinds(first)[-1], "EMIT")
        second = engine.ingest(event(code=43, agent=1000), now + 2 * DEDUP_NS + 2)
        self.assertEqual(kinds(second)[-1], "SUPPRESS")
        self.assertEqual(second[-1].note, "storm")

    def test_critical_message_clipped(self) -> None:
        engine = Engine()
        now = self.storm(engine)
        out = engine.ingest(
            event("CRITICAL", code=44, agent=2000, message="x" * 200),
            now + DEDUP_NS + 1,
        )
        self.assertEqual(kinds(out)[-1], "EMIT")
        self.assertEqual(len(out[-1].alert.event.message), 64)

    def test_quiet_exit(self) -> None:
        engine = Engine()
        now = self.storm(engine)
        out = engine.tick(now + STORM_QUIET_NS + 1)
        self.assertEqual(kinds(out), ["STORM_EXIT"])
        self.assertFalse(engine.storm)


class RecoverTest(unittest.TestCase):
    def test_recover_without_alert(self) -> None:
        engine = Engine()
        self.assertEqual(kinds(engine.recover(event(), 0)), ["IGNORE"])

    def test_pending_then_refire(self) -> None:
        engine = Engine()
        engine.ingest(event(), 0)
        out = engine.recover(event(), DEDUP_NS + 1)
        self.assertEqual(kinds(out), ["PENDING"])
        alert = next(iter(engine.active.values()))
        self.assertEqual(alert.state, PENDING)
        self.assertEqual(alert.deadline_ns, DEDUP_NS + 1 + AUTO_WARN_NS)

        repeat = engine.recover(event(), 2 * DEDUP_NS)
        self.assertEqual(kinds(repeat), ["DEDUP"])

        back = engine.ingest(event(), 3 * DEDUP_NS)
        self.assertEqual(kinds(back), ["REFIRE"])
        self.assertEqual(alert.state, FIRING)
        self.assertEqual(alert.deadline_ns, 0)

    def test_deadline_resolves(self) -> None:
        engine = Engine()
        engine.ingest(event(), 0)
        engine.recover(event(), DEDUP_NS + 1)
        deadline = DEDUP_NS + 1 + AUTO_WARN_NS
        out = engine.tick(deadline)
        self.assertEqual(kinds(out), ["RESOLVE"])
        self.assertEqual(engine.active, {})
        self.assertEqual(engine.counts["resolved"], 1)
        self.assertEqual(out[-1].alert.state, RESOLVED)


class AckTest(unittest.TestCase):
    def test_ack_and_unknown(self) -> None:
        engine = Engine()
        engine.ingest(event(), 0)
        alert = next(iter(engine.active.values()))
        out = engine.ack(alert.alert_id, "ops", "checked", 10)
        self.assertEqual(kinds(out), ["ACK"])
        self.assertEqual(out[-1].alert.ack_by, "ops")
        self.assertEqual(engine.active, {})
        self.assertEqual(engine.counts["resolved"], 1)
        self.assertEqual(kinds(engine.ack("nope", "ops", "", 11)), ["IGNORE"])

    def test_fields_clipped(self) -> None:
        engine = Engine()
        engine.ingest(event(), 0)
        alert = next(iter(engine.active.values()))
        out = engine.ack(alert.alert_id, "o" * 200, "n" * 200, 10)
        self.assertEqual(len(out[-1].alert.ack_by), 64)
        self.assertEqual(len(out[-1].alert.ack_note), 96)


class EscalateTest(unittest.TestCase):
    def test_critical_escalates_and_repeats(self) -> None:
        engine = Engine()
        engine.ingest(event("CRITICAL"), 0)
        first = engine.tick(ESCALATE_NS)
        self.assertEqual(kinds(first), ["ESCALATE"])
        alert = first[-1].alert
        self.assertEqual(alert.next_ns, 2 * ESCALATE_NS)
        again = engine.tick(2 * ESCALATE_NS)
        self.assertEqual(kinds(again), ["ESCALATE"])
        self.assertEqual(engine.counts["escalated"], 2)

    def test_warning_does_not_escalate(self) -> None:
        engine = Engine()
        engine.ingest(event("WARNING"), 0)
        self.assertEqual(engine.tick(ESCALATE_NS), [])


class StatusTest(unittest.TestCase):
    def test_shape(self) -> None:
        doc = Engine().status(5)
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(doc["active"], 0)
        self.assertFalse(doc["storm"])
        self.assertEqual(doc["updated_ns"], 5)
        self.assertEqual(doc["counts"]["received"], 0)


if __name__ == "__main__":
    unittest.main()
