# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 告警规则测试
#
# 规则加载最怕「拼错键被当成没有规则」：这里逐项验证未知键、坏窗口、重名
# 与坏 schema 都必须报错，并确认匹配条件按来源、码、级别与标签子集生效。

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "daemons"))

from alert_d.model import AlertEvent, Severity  # noqa: E402
from alert_d.rules import (  # noqa: E402
    Rule, RuleError, load_rules, parse_window,
)

NS = 1_000_000_000
DEFAULT = ROOT / "services" / "daemons" / "alert_d" / "rules"


def event(**kw) -> AlertEvent:
    base = {"ts_ns": 0, "severity": "WARNING", "source": "net_d", "code": 9}
    base.update(kw)
    return AlertEvent.build(base)


class WindowTest(unittest.TestCase):
    def test_scales(self) -> None:
        self.assertEqual(parse_window("1s"), NS)
        self.assertEqual(parse_window("5m"), 300 * NS)
        self.assertEqual(parse_window("24h"), 86400 * NS)

    def test_rejects_bad_text(self) -> None:
        for value in ("0s", "25h", "5x", "m", 300, None):
            with self.subTest(value=value):
                with self.assertRaises(RuleError):
                    parse_window(value)


class MatchTest(unittest.TestCase):
    def test_empty_rule_matches_all(self) -> None:
        rule = Rule("all", NS, 1)
        self.assertTrue(rule.match(event()))
        self.assertTrue(rule.match(event(severity="CRITICAL")))

    def test_fields_and_label_subset(self) -> None:
        rule = Rule("net", NS, 1, source=3, code=9, severity=Severity.WARNING,
                    labels=(("zone", "dmz"),))
        self.assertTrue(rule.match(event(labels={"zone": "dmz", "x": "y"})))
        self.assertFalse(rule.match(event(labels={"zone": "lan"})))
        self.assertFalse(rule.match(event(code=8, labels={"zone": "dmz"})))
        self.assertFalse(rule.match(event(source="macro_d", labels={"zone": "dmz"})))
        self.assertFalse(rule.match(event(severity="INFO", labels={"zone": "dmz"})))


class LoadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, name: str, doc) -> None:
        path = self.dir / name
        path.write_text(
            doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")

    def test_shipped_defaults(self) -> None:
        rules = load_rules(DEFAULT)
        self.assertEqual(len(rules), 3)
        self.assertEqual([rule.name for rule in rules],
                         ["sched_brief", "mem_brief", "net_brief"])
        self.assertTrue(all(rule.severity is Severity.INFO for rule in rules))

    def test_single_file_directory(self) -> None:
        self.write("10-a.json", {
            "schema": 1,
            "rules": [{"name": "net_brief", "window": "1m", "max_emit": 2,
                       "match": {"source": "net_d", "code": 9,
                                 "severity": "WARNING", "labels": {"k": "v"}},
                       "note": "n"}],
        })
        rules = load_rules(self.dir)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].source, 3)
        self.assertEqual(rules[0].code, 9)
        self.assertEqual(rules[0].labels, (("k", "v"),))
        self.assertEqual(rules[0].max_emit, 2)
        self.assertEqual(rules[0].note, "n")

    def test_missing_directory(self) -> None:
        with self.assertRaisesRegex(RuleError, "no rules directory"):
            load_rules(self.dir / "absent")

    def test_duplicate_names(self) -> None:
        for name in ("10-a.json", "20-b.json"):
            self.write(name, {"schema": 1, "rules": [
                {"name": "same", "window": "1m"}]})
        with self.assertRaisesRegex(RuleError, "duplicate"):
            load_rules(self.dir)

    def test_rejects_bad_documents(self) -> None:
        docs = [
            "{not json",
            {"schema": 2, "rules": []},
            {"schema": 1, "rules": {}},
            {"schema": 1, "rules": [], "extra": 1},
            {"schema": 1, "rules": [{"name": "Bad-Name", "window": "1m"}]},
            {"schema": 1, "rules": [{"name": "ok", "window": "1m", "x": 1}]},
            {"schema": 1, "rules": [{"name": "ok", "window": "1m",
                                     "max_emit": -1}]},
            {"schema": 1, "rules": [{"name": "ok", "window": "1m",
                                     "match": {"source": "nope"}}]},
            {"schema": 1, "rules": [{"name": "ok", "window": "1m",
                                     "match": {"bogus": 1}}]},
            {"schema": 1, "rules": [{"name": "ok", "window": "1m",
                                     "match": {"code": True}}]},
            {"schema": 1, "rules": [{"name": "ok", "window": "1m",
                                     "note": "n" * 97}]},
            {"schema": 1, "rules": ["not-an-object"]},
        ]
        for doc in docs:
            with self.subTest(doc=doc):
                self.write("10-bad.json", doc)
                with self.assertRaises(RuleError):
                    load_rules(self.dir)

    def test_skips_empty_files_and_accepts_sequence(self) -> None:
        self.write("10-a.json", {"schema": 1, "rules": []})
        self.write("20-b.json", {"schema": 1, "rules": [
            {"name": "one", "window": "1m"}]})
        missing = self.dir / "absent"
        rules = load_rules([self.dir, missing])
        self.assertEqual([rule.name for rule in rules], ["one"])


if __name__ == "__main__":
    unittest.main()
