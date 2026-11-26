# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 记忆仓库测试
#
# 覆盖布局落盘、块切分、索引与校验和、墓碑删除、索引重建、原子替换、路径
# 越界与符号链接拒绝。

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))
sys.path.insert(0, str(ROOT / "services"))

from memory.model import BadInput, BadState, BadStore, Item  # noqa: E402
from memory.store import (  # noqa: E402
    CHECKSUM,
    INDEX,
    META,
    Store,
)


def sample(count: int, size: int = 40) -> list:
    return [Item(f"item{i}", b"z" * size) for i in range(1, count + 1)]


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = Store(str(self.base / "mem"), 4)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_layout_after_write(self) -> None:
        self.store.save_items("L3", "7", sample(3), owner="team")
        folder = self.base / "mem" / "l3" / "7"
        for name in (META, INDEX, CHECKSUM):
            self.assertTrue((folder / name).is_file(), name)
        blocks = sorted((folder / "data").glob("*.airyblk"))
        self.assertEqual([path.name for path in blocks], ["000001.airyblk"])
        meta = json.loads((folder / META).read_text(encoding="utf-8"))
        self.assertEqual(meta["owner"], "team")
        self.assertEqual(meta["level"], "L3")
        self.assertEqual(meta["block_kb"], 4)

    def test_items_round_trip(self) -> None:
        rows = sample(5)
        rows[2] = Item("item3", b"secret", True)
        self.store.save_items("L4", "9", rows)
        back = {item.id: item for item in self.store.items("L4", "9")}
        self.assertEqual(sorted(back), [f"item{i}" for i in range(1, 6)])
        self.assertTrue(back["item3"].sensitive)
        self.assertEqual(back["item1"].data, b"z" * 40)

    def test_small_blocks_split(self) -> None:
        small = Store(str(self.base / "small"), 1)
        small.save_items("L3", "1", sample(20, 100))
        self.assertGreater(len(small.read_blocks("L3", "1")), 1)

    def test_checksum_detects_tampering(self) -> None:
        self.store.save_items("L3", "2", sample(2))
        self.assertEqual(self.store.verify("L3", "2"), "ok")
        target = self.base / "mem" / "l3" / "2" / CHECKSUM
        target.write_text("deadbeef  data/000001.airyblk\n", encoding="utf-8")
        self.assertEqual(self.store.verify("L3", "2"), "mismatch")

    def test_rebuild_index_from_blocks(self) -> None:
        self.store.save_items("L3", "3", sample(4))
        folder = self.base / "mem" / "l3" / "3"
        (folder / INDEX).unlink()
        self.assertFalse((folder / INDEX).is_file())
        result = self.store.rebuild("L3", "3")
        self.assertEqual(result, {"blocks": 1, "items": 4})
        doc = json.loads((folder / INDEX).read_text(encoding="utf-8"))
        self.assertIn("item1", doc["items"])
        self.assertEqual(self.store.verify("L3", "3"), "ok")

    def test_remove_marks_tombstones(self) -> None:
        self.store.save_items("L3", "4", sample(6))
        found = self.store.remove_items("L3", "4", ["item1", "item2"])
        self.assertEqual(found, 2)
        self.assertEqual([item.id for item in self.store.items("L3", "4")],
                         ["item3", "item4", "item5", "item6"])
        rows = self.store.read_blocks("L3", "4")["000001.airyblk"]
        self.assertEqual(sum(1 for row in rows if row.deleted), 2)
        with self.assertRaises(BadState):
            self.store.remove_items("L3", "4", ["absent"])

    def test_replace_all_drops_old_blocks(self) -> None:
        self.store.save_items("L3", "5", sample(12, 200))
        before = len(self.store.read_blocks("L3", "5"))
        self.store.replace_all("L3", "5", sample(2, 200))
        self.assertEqual(len(self.store.read_blocks("L3", "5")), 1)
        self.assertGreaterEqual(before, 1)
        self.assertFalse((self.base / "mem" / "l3" / "5" / "data.old").exists())

    def test_drop_removes_container(self) -> None:
        self.store.save_items("L3", "6", sample(2))
        removed, freed = self.store.drop("L3", "6")
        self.assertGreaterEqual(removed, 4)
        self.assertGreater(freed, 0)
        self.assertFalse(self.store.exists("L3", "6"))
        self.assertFalse((self.base / "mem" / "l3" / "6").exists())

    def test_agent_id_traversal_rejected(self) -> None:
        for bad in ("../etc", "1/../../x", "0", "abc"):
            with self.assertRaises(BadInput):
                self.store.folder("L3", bad)

    def test_symlink_container_rejected(self) -> None:
        outside = self.base / "outside"
        self.store.save_items("L3", "8", sample(1))
        os.rename(self.base / "mem" / "l3" / "8", outside)
        os.symlink(outside, self.base / "mem" / "l3" / "8")
        with self.assertRaises(BadStore):
            self.store.read_blocks("L3", "8")

    def test_archive_folder_only_for_l4(self) -> None:
        self.assertEqual(
            self.store.folder("L4", "1", archived=True),
            self.base / "mem" / "l4.archive" / "1")
        with self.assertRaises(BadInput):
            self.store.folder("L3", "1", archived=True)


if __name__ == "__main__":
    unittest.main()
