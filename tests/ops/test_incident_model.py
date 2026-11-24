# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 事件模型契约测试
#
# 状态机的边是台账、命令行与复盘共用的唯一判据，这里逐条钉住合法边与
# 拒绝边；时间戳必须在纳秒精度上往返一致，否则复盘时间线会错位。

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))

from incident.model import (  # noqa: E402
    ALLOWED, DETECTED, ESCALATED, POSTMORTEM, RECOVERING, REOPENED,
    RESOLVED, RESPONDING, TRIAGED, TITLE_MAX, BadInput, BadState, Category,
    Incident, Severity, State, check_id, day_key, iso, make_id, names_of,
    open_incident, parse_iso, text_of,
)

BASE = 1_700_000_000 * 1_000_000_000 + 123_456_789
DAY = 86400 * 1_000_000_000


def build(**kw) -> Incident:
    base = {
        "incident_id": make_id(day_key(BASE), 1),
        "severity": "P1",
        "category": "system",
        "title": "daemon 反复重启",
        "detected_at": BASE,
    }
    base.update(kw)
    return Incident(**base)


class SeverityTest(unittest.TestCase):
    def test_parse_names_and_numbers(self) -> None:
        self.assertIs(Severity.parse("p0"), Severity.P0)
        self.assertIs(Severity.parse(" P2 "), Severity.P2)
        self.assertIs(Severity.parse(1), Severity.P1)
        self.assertIs(Severity.parse(Severity.P3), Severity.P3)

    def test_parse_rejects_bad_values(self) -> None:
        for value in (True, 4, -1, "P9", None, 1.0):
            with self.subTest(value=value):
                with self.assertRaises(BadInput):
                    Severity.parse(value)

    def test_rank_follows_severity(self) -> None:
        self.assertLess(Severity.P0.rank, Severity.P1.rank)
        self.assertLess(Severity.P1.rank, Severity.P3.rank)


class CategoryTest(unittest.TestCase):
    def test_parse_names(self) -> None:
        self.assertIs(Category.parse("SECURITY"), Category.SECURITY)
        self.assertIs(Category.parse(" resource "), Category.RESOURCE)

    def test_parse_rejects_bad_values(self) -> None:
        for value in ("disk", "", None, 1):
            with self.subTest(value=value):
                with self.assertRaises(BadInput):
                    Category.parse(value)


class StateTest(unittest.TestCase):
    def test_all_states_have_edges(self) -> None:
        for state in State:
            self.assertIn(state, ALLOWED)

    def test_postmortem_is_terminal(self) -> None:
        self.assertEqual(ALLOWED[POSTMORTEM], ())

    def test_parse_names(self) -> None:
        self.assertIs(State.parse("triaged"), TRIAGED)
        self.assertIs(State.parse("Reopened"), REOPENED)
        with self.assertRaises(BadInput):
            State.parse("CLOSED")


class StampTest(unittest.TestCase):
    def test_round_trip_keeps_nanoseconds(self) -> None:
        text = iso(BASE)
        self.assertTrue(text.endswith("Z"))
        self.assertEqual(len(text.split(".")[1]), 10)
        self.assertEqual(parse_iso(text), BASE)

    def test_rejects_negative_and_bad_text(self) -> None:
        with self.assertRaises(BadInput):
            iso(-1)
        for text in ("", "2026-01-02", "2026-01-02T03:04:05", 5):
            with self.subTest(text=text):
                with self.assertRaises(BadInput):
                    parse_iso(text)

    def test_day_key_and_id(self) -> None:
        self.assertEqual(day_key(BASE), "20231114")
        self.assertEqual(make_id("20231114", 7), "INC-20231114-007")
        for seq in (0, 1000):
            with self.assertRaises(BadInput):
                make_id("20231114", seq)
        for ident in ("INC-20231114-01", "INC-20231114-0001", "inc-20231114-001"):
            with self.subTest(ident=ident):
                with self.assertRaises(BadInput):
                    check_id(ident)


class TextTest(unittest.TestCase):
    def test_text_is_trimmed(self) -> None:
        self.assertEqual(text_of("  abc  ", "title", 8), "abc")

    def test_text_rejects_empty_and_long(self) -> None:
        with self.assertRaises(BadInput):
            text_of("   ", "title", 8)
        with self.assertRaises(BadInput):
            text_of("x" * 9, "title", 8)
        with self.assertRaises(BadInput):
            text_of(5, "title", 8)

    def test_names_are_unique(self) -> None:
        self.assertEqual(names_of(["a", "a", " b "], "daemon"), ["a", "b"])
        with self.assertRaises(BadInput):
            names_of("a", "daemon")
        with self.assertRaises(BadInput):
            names_of(["a", ""], "daemon")


class IncidentTest(unittest.TestCase):
    def test_open_records_detection(self) -> None:
        inc = open_incident(make_id(day_key(BASE), 1), "P0", "security",
                            "越权访问", BASE, "sec_d", source="sec_d",
                            detail="LSM 拒绝", alerts=["a1"],
                            agents=[3, 1, 3], daemons=["sec_d"])
        self.assertIs(inc.status, DETECTED)
        self.assertEqual(inc.affected_agents, [1, 3])
        self.assertEqual(inc.timeline[0]["event"], "detected")
        self.assertEqual(inc.alerts, ["a1"])

    def test_rejects_bad_fields(self) -> None:
        with self.assertRaises(BadInput):
            build(title="x" * (TITLE_MAX + 1))
        with self.assertRaises(BadInput):
            build(detected_at=0)
        with self.assertRaises(BadInput):
            build(affected_agents=[-1])
        with self.assertRaises(BadInput):
            build(affected_agents=[True])

    def test_switch_walks_the_happy_path(self) -> None:
        inc = build()
        path = (TRIAGED, RESPONDING, RECOVERING, RESOLVED)
        for index, target in enumerate(path, 1):
            inc.switch(target, "operator", BASE + index * 10)
        self.assertIs(inc.status, RESOLVED)
        self.assertGreater(inc.resolved_at, 0)
        self.assertEqual([row["event"] for row in inc.timeline][-4:],
                         ["triaged", "responded", "recovered", "resolved"])

    def test_switch_rejects_illegal_edge(self) -> None:
        inc = build()
        with self.assertRaises(BadState):
            inc.switch(RESOLVED, "operator", BASE + 1)

    def test_escalate_and_reopen(self) -> None:
        inc = build()
        inc.switch(TRIAGED, "operator", BASE + 1)
        inc.switch(RESPONDING, "operator", BASE + 2)
        inc.switch(ESCALATED, "operator", BASE + 3)
        inc.switch(RESPONDING, "operator", BASE + 4)
        inc.switch(RECOVERING, "operator", BASE + 5)
        inc.switch(RESOLVED, "operator", BASE + 6)
        inc.switch(REOPENED, "operator", BASE + 7)
        self.assertEqual(inc.resolved_at, 0)
        self.assertIs(inc.status, REOPENED)

    def test_action_validates_result(self) -> None:
        inc = build()
        inc.action("restart", "operator", BASE + 1, "failed", "退出码 1")
        self.assertEqual(inc.response_actions[0]["result"], "failed")
        with self.assertRaises(BadInput):
            inc.action("restart", "operator", BASE + 1, "maybe")

    def test_json_round_trip(self) -> None:
        inc = build(root_cause="配置错误", source="macro_d")
        inc.switch(TRIAGED, "operator", BASE + 1)
        inc.action("restart", "operator", BASE + 2)
        doc = inc.to_json()
        self.assertNotIn("resolved_at", doc)
        self.assertEqual(doc["root_cause"], "配置错误")
        again = Incident.from_json(doc)
        self.assertEqual(again.to_json(), doc)

    def test_from_json_rejects_bad_doc(self) -> None:
        doc = build().to_json()
        for key, value in (("schema", 2), ("timeline", {}),
                           ("response_actions", "x")):
            bad = dict(doc)
            bad[key] = value
            with self.subTest(key=key):
                with self.assertRaises(BadInput):
                    Incident.from_json(bad)


if __name__ == "__main__":
    unittest.main()
