# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 事件台账持久化测试
#
# 编号必须按日递增且不重号；归档只能在保留期外发生，安全类另按更长的
# 保留期判定；落盘损坏时要报台账错误而不是静默跳过。

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))

from incident.model import (  # noqa: E402
    BadInput, BadStore, Category, Severity, State, day_key, make_id,
)
from incident.store import Store  # noqa: E402

BASE = 1_700_000_000 * 1_000_000_000
DAY = 86400 * 1_000_000_000


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make(self, stamp: int = BASE, severity: str = "P1",
             category: str = "system", title: str = "事件") -> str:
        inc = self.store.create(stamp, severity, category, title, "operator")
        return inc.incident_id

    def test_sequence_within_a_day(self) -> None:
        day = day_key(BASE)
        self.assertEqual(self.make(), make_id(day, 1))
        self.assertEqual(self.make(), make_id(day, 2))
        self.assertEqual(self.make(BASE + DAY), make_id(day_key(BASE + DAY), 1))
        self.assertEqual(self.store.ids(), sorted(self.store.ids()))

    def test_round_trip(self) -> None:
        ident = self.make(title="配置热更新失败")
        inc = self.store.load(ident)
        self.assertEqual(inc.title, "配置热更新失败")
        self.assertIs(inc.status, State.DETECTED)
        self.assertEqual(inc.timeline[0]["event"], "detected")

    def test_load_reports_missing_and_corrupt(self) -> None:
        with self.assertRaises(BadStore):
            self.store.load(make_id(day_key(BASE), 9))
        ident = self.make()
        (self.root / f"{ident}.json").write_text("{", encoding="utf-8")
        with self.assertRaises(BadStore):
            self.store.load(ident)

    def test_list_filters_and_sorts(self) -> None:
        low = self.make(severity="P3", title="低")
        high = self.make(severity="P0", title="高")
        mid = self.make(severity="P1", title="中")
        rows = self.store.list()
        self.assertEqual([inc.incident_id for inc in rows], [high, mid, low])
        self.assertEqual(len(self.store.list(severity=Severity.P0)), 1)
        self.assertEqual(len(self.store.list(limit=2)), 2)

    def test_find_alert(self) -> None:
        inc = self.store.create(BASE, "P0", "security", "越权", "sec_d",
                                alerts=["ALT-1"])
        found = self.store.find_alert("ALT-1")
        self.assertIsNotNone(found)
        self.assertEqual(found.incident_id, inc.incident_id)

    def test_report_is_written(self) -> None:
        ident = self.make()
        path = self.store.report(self.store.load(ident), "# 复盘\n")
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_text(encoding="utf-8"), "# 复盘\n")

    def test_prune_dry_run_keeps_records(self) -> None:
        ident = self.make()
        inc = self.store.load(ident)
        for target in (State.TRIAGED, State.RESPONDING, State.RECOVERING,
                       State.RESOLVED):
            inc.switch(target, "operator", BASE + 10)
        self.store.save(inc)
        picked, moved = self.store.prune(90, BASE + 100 * DAY)
        self.assertEqual([row.incident_id for row in picked], [ident])
        self.assertEqual(moved, [])
        self.assertTrue((self.root / f"{ident}.json").is_file())

    def test_prune_apply_archives(self) -> None:
        ident = self.make()
        inc = self.store.load(ident)
        for target in (State.TRIAGED, State.RESPONDING, State.RECOVERING,
                       State.RESOLVED):
            inc.switch(target, "operator", BASE + 10)
        self.store.save(inc)
        self.store.report(inc, "# 复盘\n")
        stamp = BASE + 100 * DAY
        picked, moved = self.store.prune(90, stamp, apply=True)
        self.assertEqual(moved, [ident])
        self.assertFalse((self.root / f"{ident}.json").exists())
        month = "202311"
        body = (self.root / "archive" / f"{month}.jsonl").read_text(
            encoding="utf-8")
        self.assertIn(ident, body)
        self.assertIn("archived_at", body)
        self.assertIn("# 复盘", body)

    def test_security_keeps_longer(self) -> None:
        ident = self.make(category="security")
        inc = self.store.load(ident)
        for target in (State.TRIAGED, State.RESPONDING, State.RECOVERING,
                       State.RESOLVED):
            inc.switch(target, "operator", BASE + 10)
        self.store.save(inc)
        picked, _ = self.store.prune(90, BASE + 200 * DAY)
        self.assertEqual(picked, [])
        picked, _ = self.store.prune(90, BASE + 400 * DAY)
        self.assertEqual([row.incident_id for row in picked], [ident])

    def test_prune_rejects_bad_days(self) -> None:
        for days, sec in ((0, 365), (90, 0)):
            with self.subTest(days=days, sec=sec):
                with self.assertRaises(BadInput):
                    self.store.prune(days, BASE, sec_days=sec)

    def test_stats_counts_closed(self) -> None:
        ident = self.make()
        inc = self.store.load(ident)
        for target in (State.TRIAGED, State.RESPONDING, State.RECOVERING):
            inc.switch(target, "operator", BASE + 10)
        inc.switch(State.RESOLVED, "operator", BASE + 60 * 1_000_000_000)
        inc.action("restart", "operator", BASE + 20)
        self.store.save(inc)
        self.make(severity="P2", title="未结")
        doc = self.store.stats("202311", BASE + 100 * DAY)
        self.assertEqual(doc["total"], 2)
        self.assertEqual(doc["open"], 1)
        self.assertEqual(doc["closed"], 1)
        self.assertEqual(doc["mttr_s"], 60.0)
        self.assertEqual(doc["actions"], 1)
        self.assertEqual(doc["by_severity"], {"P1": 1, "P2": 1})

    def test_stats_rejects_bad_month(self) -> None:
        for month in ("2023", "20231a", ""):
            with self.subTest(month=month):
                with self.assertRaises(BadInput):
                    self.store.stats(month, BASE)

    def test_category_is_enum_on_load(self) -> None:
        ident = self.make(category="network")
        self.assertIs(self.store.load(ident).category, Category.NETWORK)


if __name__ == "__main__":
    unittest.main()
