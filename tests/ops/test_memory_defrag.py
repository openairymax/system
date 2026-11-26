# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 碎片整理测试
#
# 覆盖 §6.2 阈值判定、时间窗口与 --now 跳过、真实回收字节、索引重建后的项
# 不丢失、状态文件与多 Agent 批量整理。

from __future__ import annotations

import datetime
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))
sys.path.insert(0, str(ROOT / "services"))

from memory import config, defrag, stats  # noqa: E402
from memory.model import NS, BadInput, BadState, BadStore, Item  # noqa: E402
from memory.store import Store  # noqa: E402


def local_stamp(hour: int, minute: int = 0) -> int:
    moment = datetime.datetime(2026, 3, 4, hour, minute).timestamp()
    return int(moment * NS)


class DefragTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = Store(str(self.base / "mem"), 4)
        self.cfg = config.load(str(self.base / "absent.conf"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def fill(self, agent: str, count: int = 20, size: int = 200) -> None:
        self.store.save_items("L3", agent,
                              [Item(f"i{n}", b"d" * size)
                               for n in range(1, count + 1)])

    def test_status_starts_empty(self) -> None:
        self.assertEqual(defrag.status(self.store), {"state": "none"})

    def test_window_outside_rejected(self) -> None:
        self.fill("1")
        with self.assertRaises(BadState):
            defrag.run(self.store, self.cfg, "L3", "1",
                       stamp=local_stamp(12))
        self.assertEqual(self.cfg["defrag_window"], "02:00-05:00")

    def test_now_skips_window(self) -> None:
        self.fill("1")
        doc = defrag.run(self.store, self.cfg, "L3", "1",
                         stamp=local_stamp(12), now=True)
        self.assertIn(doc["state"], ("done", "skipped"))

    def test_threshold_not_met_skips(self) -> None:
        self.store.save_items(
            "L3", "2", [Item(f"i{n}", b"k" * 3000) for n in range(1, 61)])
        defrag_stamp = datetime.datetime(2026, 3, 4, 2, 10).timestamp()
        self.store.mark_defrag("L3", "2", int(defrag_stamp * NS))
        doc = defrag.run(self.store, self.cfg, "L3", "2",
                         stamp=local_stamp(3), now=True)
        self.assertEqual(doc["state"], "skipped")
        self.assertEqual(doc["reason"], "threshold-not-met")

    def test_defrag_reclaims_and_keeps_items(self) -> None:
        self.fill("3")
        self.store.remove_items("L3", "3", [f"i{n}" for n in range(1, 16)])
        before = stats.level_stats(self.store, "L3", "3")
        self.assertGreater(before["fragmentation_pct"], 30.0)
        doc = defrag.run(self.store, self.cfg, "L3", "3", now=True)
        self.assertEqual(doc["state"], "done")
        self.assertEqual(doc["progress_pct"], 100)
        self.assertEqual(doc["items"], 5)
        self.assertGreater(doc["freed_bytes"], 0)
        self.assertEqual(doc["after"]["fragmentation_pct"], 0.0)
        self.assertEqual(sorted(item.id for item in
                                self.store.items("L3", "3")),
                         [f"i{n}" for n in range(16, 21)])
        self.assertEqual(self.store.verify("L3", "3"), "ok")

    def test_state_file_records_run(self) -> None:
        self.fill("4")
        self.store.remove_items("L3", "4", [f"i{n}" for n in range(1, 6)])
        defrag.run(self.store, self.cfg, "L3", "4", now=True)
        doc = defrag.status(self.store)
        self.assertEqual(doc["state"], "done")
        self.assertEqual(doc["agent"], "4")
        self.assertIn("freed_bytes", doc)
        self.assertIn("duration_ms", doc)

    def test_mark_defrag_timestamp(self) -> None:
        self.fill("5")
        self.store.remove_items("L3", "5", [f"i{n}" for n in range(1, 16)])
        self.assertIsNone(self.store.meta("L3", "5")["last_defrag_at"])
        defrag.run(self.store, self.cfg, "L3", "5", now=True)
        self.assertIsNotNone(self.store.meta("L3", "5")["last_defrag_at"])

    def test_run_all_handles_each_agent(self) -> None:
        for agent in ("6", "7"):
            self.fill(agent)
            self.store.remove_items("L3", agent,
                                    [f"i{n}" for n in range(1, 16)])
        docs = defrag.run_all(self.store, self.cfg, "L3", now=True)
        self.assertEqual(sorted(doc["agent"] for doc in docs), ["6", "7"])
        self.assertTrue(all(doc["state"] == "done" for doc in docs))
        self.assertEqual(sorted(agent for agent in self.store.ids("L3")),
                         ["6", "7"])

    def test_missing_agent_rejected(self) -> None:
        with self.assertRaises(BadStore):
            defrag.run(self.store, self.cfg, "L3", "99", now=True)
        with self.assertRaises(BadInput):
            defrag.run(self.store, self.cfg, "L2", "99", now=True)
        with self.assertRaises(BadInput):
            defrag.run(self.store, self.cfg, "L3", "../1", now=True)


if __name__ == "__main__":
    unittest.main()
