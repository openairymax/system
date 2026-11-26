# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 记忆统计测试
#
# 覆盖只报磁盘事实的字段集合、空容器语义、墓碑产生的碎片、索引占比、归档
# 占用与跨层汇总。

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))
sys.path.insert(0, str(ROOT / "services"))

from memory import stats  # noqa: E402
from memory.model import BadStore, Item  # noqa: E402
from memory.store import Store  # noqa: E402

FIELDS = {"items", "blocks", "data_bytes", "live_bytes", "dead_items",
          "dead_bytes", "index_bytes", "index_ratio_pct", "utilization_pct",
          "fragmentation_pct", "checksum", "block_kb", "owner", "updated_at"}


class StatsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = Store(str(self.base / "mem"), 4)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_empty_container_is_cold(self) -> None:
        row = stats.level_stats(self.store, "L3", "7")
        self.assertFalse(row["exists"])
        self.assertEqual(row["items"], 0)
        self.assertEqual(row["fragmentation_pct"], 0.0)
        self.assertEqual(row["checksum"], "missing")

    def test_live_facts_match_disk(self) -> None:
        self.store.save_items("L3", "7", [Item(f"i{n}", b"q" * 50)
                                          for n in range(1, 6)])
        row = stats.level_stats(self.store, "L3", "7")
        folder = self.base / "mem" / "l3" / "7" / "data"
        on_disk = sum(path.stat().st_size
                      for path in folder.glob("*.airyblk"))
        self.assertTrue(row["exists"])
        self.assertEqual(row["items"], 5)
        self.assertEqual(row["blocks"], 1)
        self.assertEqual(row["data_bytes"], on_disk)
        self.assertEqual(row["dead_items"], 0)
        self.assertEqual(row["fragmentation_pct"], 0.0)
        self.assertGreater(row["utilization_pct"], 95.0)
        self.assertEqual(row["checksum"], "ok")
        self.assertTrue(FIELDS.issubset(set(row)))

    def test_tombstones_raise_fragmentation(self) -> None:
        self.store.save_items("L3", "8", [Item(f"i{n}", b"w" * 80)
                                          for n in range(1, 11)])
        self.store.remove_items("L3", "8", [f"i{n}" for n in range(1, 8)])
        row = stats.level_stats(self.store, "L3", "8")
        self.assertEqual(row["items"], 3)
        self.assertEqual(row["dead_items"], 7)
        self.assertGreater(row["dead_bytes"], 0)
        self.assertGreater(row["fragmentation_pct"], 30.0)
        self.assertLess(row["utilization_pct"], 100.0)

    def test_index_ratio_uses_real_sizes(self) -> None:
        self.store.save_items("L3", "9", [Item(f"i{n}", b"e" * 200)
                                          for n in range(1, 4)])
        row = stats.level_stats(self.store, "L3", "9")
        folder = self.base / "mem" / "l3" / "9"
        index = (folder / "index.airyidx").stat().st_size
        self.assertEqual(row["index_bytes"], index)
        self.assertAlmostEqual(row["index_ratio_pct"],
                               stats.index_ratio(index, row["data_bytes"]), 2)

    def test_archive_bytes_counts_files(self) -> None:
        data = self.store.datadir("L4", "4", archived=True)
        data.mkdir(parents=True)
        (data / "000001.airyblk").write_bytes(b"x" * 12)
        self.assertEqual(stats.archive_bytes(self.store, "4"), 12)
        self.assertEqual(stats.archive_bytes(self.store, "5"), 0)

    def test_agent_status_covers_both_levels(self) -> None:
        self.store.save_items("L4", "3", [Item("only", b"k")])
        report = stats.agent_status(self.store, "3")
        self.assertTrue(report["exists"])
        self.assertTrue(report["levels"]["L4"]["exists"])
        self.assertFalse(report["levels"]["L3"]["exists"])
        self.assertIn("archive_bytes", report)

    def test_require_rejects_missing(self) -> None:
        with self.assertRaises(BadStore):
            stats.require(stats.agent_status(self.store, "11"))
        self.store.save_items("L3", "12", [Item("live", b"v")])
        stats.require(stats.agent_status(self.store, "12"))


if __name__ == "__main__":
    unittest.main()
