# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 告警持久化测试
#
# 活动告警、状态与归档三份文件的读写都必须可往返；归档分片按首次出现的
# 墙钟日期，历史查询按行内时间戳过滤，损坏文件要报出具体位置。

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "daemons"))
sys.path.insert(0, str(ROOT / "services"))

from alert_d.model import AlertEvent, make_alert  # noqa: E402
from alert_d.store import ACTIVE, STATUS, Store  # noqa: E402

NS = 1_000_000_000
DAY = 1767312000 * NS  # 2026-01-02T00:00:00Z


def event(severity: str = "WARNING", code: int = 7, agent: int = 0,
          source: str = "net_d", ts_ns: int = DAY) -> AlertEvent:
    return AlertEvent.build({
        "ts_ns": ts_ns, "severity": severity, "code": code,
        "agent": agent, "source": source,
    })


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "state")

    def test_active_roundtrip_sorted(self) -> None:
        low = make_alert(event("INFO", code=1), 10)
        high = make_alert(event("CRITICAL", code=2), 20)
        mid = make_alert(event("WARNING", code=3), 30)
        self.store.save([low, high, mid])
        rows = self.store.load()
        self.assertEqual([row["code"] for row in rows], [2, 3, 1])
        self.assertEqual(rows[0]["severity"], "CRITICAL")
        self.assertEqual(rows[0]["alert_id"], high.alert_id)

    def test_load_missing_and_bad(self) -> None:
        self.assertEqual(self.store.load(), [])
        path = self.store.root / ACTIVE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.load()
        path.write_text(json.dumps({"schema": 99, "alerts": []}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.load()
        path.write_text(json.dumps({"schema": 1, "alerts": {}}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.load()

    def test_status_roundtrip(self) -> None:
        self.assertEqual(self.store.status(), {})
        self.store.save_status({"schema": 1, "active": 2})
        self.assertEqual(self.store.status()["active"], 2)

    def test_status_rejects_non_object(self) -> None:
        self.store.save_status({"schema": 1})
        (self.store.root / STATUS).write_text("[1]", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.status()

    def test_archive_shard_uses_wall_clock(self) -> None:
        alert = make_alert(event(), 500)
        path = self.store.append(alert)
        self.assertEqual(path.name, "20260102.jsonl")
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ts_ns"], DAY)

    def test_history_filters_and_limit(self) -> None:
        self.store.append(make_alert(event("INFO", code=1, ts_ns=DAY), 1))
        self.store.append(make_alert(event("WARNING", code=2, agent=7,
                                          ts_ns=DAY + 1), 2))
        self.store.append(make_alert(event("CRITICAL", code=3, source="mem_d",
                                          ts_ns=DAY + 2), 3))

        self.assertEqual(len(self.store.history()), 3)
        self.assertEqual([r["code"] for r in self.store.history(severity="WARNING")], [2])
        self.assertEqual([r["code"] for r in self.store.history(source="mem_d")], [3])
        self.assertEqual([r["code"] for r in self.store.history(agent=7)], [2])
        self.assertEqual([r["code"] for r in self.store.history(code=1)], [1])
        self.assertEqual(len(self.store.history(limit=2)), 2)
        self.assertEqual(
            [r["code"] for r in self.store.history(since_ns=DAY + 1)], [2, 3])

    def test_history_bad_line_reports_position(self) -> None:
        self.store.append(make_alert(event(), 1))
        bad = self.store.archive / "20260103.jsonl"
        bad.write_text("{oops\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"20260103\.jsonl:1"):
            self.store.history()

    def test_history_absent_archive(self) -> None:
        self.assertEqual(self.store.history(), [])


if __name__ == "__main__":
    unittest.main()
