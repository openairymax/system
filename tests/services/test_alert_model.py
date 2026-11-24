# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 告警契约测试
#
# 解析入口既要接受合法输入，也要对越界与类型错误给出可归因的异常；键值
# 摘要必须与标签书写顺序无关，否则同一告警会分成两条。

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "daemons"))

from alert_d.model import (  # noqa: E402
    CODE_MAX, MSG_MAX, SOURCES, AlertEvent, BadEvent, Severity, clip,
    dedup_key, make_alert, parse_labels, raise_sev, source_of,
)

NS = 1_000_000_000


def event(**kw) -> AlertEvent:
    base = {"ts_ns": 0, "severity": "WARNING", "source": "macro_d", "code": 7}
    base.update(kw)
    return AlertEvent.build(base, now=5)


class SeverityTest(unittest.TestCase):
    def test_parse_names_and_numbers(self) -> None:
        self.assertIs(Severity.parse("info"), Severity.INFO)
        self.assertIs(Severity.parse(" CRITICAL "), Severity.CRITICAL)
        self.assertIs(Severity.parse(1), Severity.WARNING)
        self.assertIs(Severity.parse(Severity.CRITICAL), Severity.CRITICAL)

    def test_parse_rejects_bad_values(self) -> None:
        for value in (True, 3, -1, "fatal", None, 1.0):
            with self.subTest(value=value):
                with self.assertRaises(BadEvent):
                    Severity.parse(value)

    def test_label_is_name(self) -> None:
        self.assertEqual(Severity.INFO.label, "INFO")


class SourceTest(unittest.TestCase):
    def test_names_and_digits_agree(self) -> None:
        self.assertEqual(len(SOURCES), 12)
        for sid, name in SOURCES.items():
            self.assertEqual(source_of(name), sid)
            self.assertEqual(source_of(str(sid)), sid)

    def test_rejects_unknown(self) -> None:
        for value in (True, 0, 13, "nosuch", None):
            with self.subTest(value=value):
                with self.assertRaises(BadEvent):
                    source_of(value)


class ClipTest(unittest.TestCase):
    def test_short_text_untouched(self) -> None:
        self.assertEqual(clip("hello"), "hello")

    def test_multibyte_not_split(self) -> None:
        text = "记" * (MSG_MAX // 3 + 10)
        out = clip(text)
        self.assertLessEqual(len(out.encode("utf-8")), MSG_MAX)
        self.assertTrue(out.startswith("记"))
        out.encode("utf-8")

    def test_custom_limit(self) -> None:
        self.assertEqual(len(clip("abcdef", 3)), 3)


class LabelTest(unittest.TestCase):
    def test_none_and_sorted(self) -> None:
        self.assertEqual(parse_labels(None), ())
        got = parse_labels({"b": "2", "a": "1"})
        self.assertEqual(got, (("a", "1"), ("b", "2")))

    def test_rejects_bad_shape(self) -> None:
        bad = [
            ["a=1"],
            {"k": 1},
            {"": "v"},
            {"k": "v" * 65},
            {f"k{idx}": "v" for idx in range(17)},
        ]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(BadEvent):
                    parse_labels(value)


class EventTest(unittest.TestCase):
    def test_defaults_and_wall_clock(self) -> None:
        got = AlertEvent.build({"severity": "INFO"})
        self.assertEqual(got.ts_ns, 0)
        self.assertEqual(got.source, 1)
        self.assertEqual(got.code, 0)
        self.assertEqual(got.agent, 0)
        self.assertEqual(got.message, "")
        self.assertEqual(got.labels, ())

    def test_missing_ts_uses_now(self) -> None:
        self.assertEqual(AlertEvent.build({"severity": "INFO"}, now=9).ts_ns, 9)

    def test_rejects_bad_fields(self) -> None:
        bad = [
            {"severity": "INFO", "ts_ns": -1},
            {"severity": "INFO", "code": CODE_MAX + 1},
            {"severity": "INFO", "agent": 1 << 32},
            {"severity": "INFO", "message": 1},
            {"severity": "INFO", "labels": {"a": 1}},
            {"severity": "INFO", "ts_ns": True},
        ]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(BadEvent):
                    AlertEvent.build(value)

    def test_non_mapping_rejected(self) -> None:
        with self.assertRaises(BadEvent):
            AlertEvent.build(["severity"])

    def test_key_ignores_label_order(self) -> None:
        left = event(labels={"a": "1", "b": "2"})
        right = event(labels={"b": "2", "a": "1"})
        self.assertEqual(left.key, right.key)
        self.assertEqual(left.key, dedup_key(1, 7, 0, (("a", "1"), ("b", "2"))))

    def test_to_json_uses_names(self) -> None:
        row = event(message="m", labels={"k": "v"}).to_json()
        self.assertEqual(row["severity"], "WARNING")
        self.assertEqual(row["source"], "macro_d")
        self.assertEqual(row["labels"], {"k": "v"})


class AlertTest(unittest.TestCase):
    def test_make_alert_deterministic(self) -> None:
        first = make_alert(event(), 100)
        again = make_alert(event(), 100)
        later = make_alert(event(), 101)
        self.assertEqual(first.alert_id, again.alert_id)
        self.assertNotEqual(first.alert_id, later.alert_id)
        self.assertEqual(len(first.alert_id), 16)
        self.assertEqual(first.first_ns, 100)

    def test_raise_sev_monotonic(self) -> None:
        alert = make_alert(event(severity="WARNING"), 0)
        raise_sev(alert, Severity.INFO)
        self.assertIs(alert.event.severity, Severity.WARNING)
        raise_sev(alert, Severity.CRITICAL)
        self.assertIs(alert.event.severity, Severity.CRITICAL)

    def test_to_json_flattens_state(self) -> None:
        row = make_alert(event(), 3).to_json()
        for key in ("alert_id", "state", "first_ns", "count", "ack_by"):
            self.assertIn(key, row)
        self.assertEqual(row["count"], 1)


if __name__ == "__main__":
    unittest.main()
