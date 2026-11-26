# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# Agent 元数据存储测试
#
# 覆盖编号分配、原子写盘后的可读性、损坏记录拒绝、归档搬迁与编号不复用。

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))
sys.path.insert(0, str(ROOT / "services"))

from agent import config  # noqa: E402
from agent.model import BadStore, State, parse_iso  # noqa: E402
from agent.store import Store  # noqa: E402

STAMP = parse_iso("2026-02-03T04:05:06Z")


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = Store(self.base / "agents")
        self.cfg = config.load(str(self.base / "absent.conf"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_create_assigns_sequential_ids(self) -> None:
        first = self.store.create(self.cfg, {"name": "a", "template": "t"},
                                  STAMP)
        second = self.store.create(self.cfg, {"name": "b", "template": "t"},
                                   STAMP)
        self.assertEqual((first.id, second.id), ("1", "2"))
        meta = self.base / "agents" / "1" / "meta.json"
        self.assertTrue(meta.is_file())
        self.assertEqual(self.store.load("1").name, "a")

    def test_rows_sorted_and_filtered(self) -> None:
        self.store.create(self.cfg, {"name": "a", "template": "t"}, STAMP)
        self.store.create(self.cfg, {"name": "b", "template": "t"},
                          STAMP + 1)
        self.assertEqual([row.id for row in self.store.rows()], ["1", "2"])
        self.assertEqual(self.store.rows("INACTIVE")[0].name, "a")
        self.assertEqual(self.store.rows("RUNNING"), [])

    def test_archive_moves_and_ids_not_reused(self) -> None:
        agent = self.store.create(self.cfg, {"name": "a", "template": "t"},
                                  STAMP)
        agent.switch(State.DEAD, "terminate", STAMP)
        self.store.save(agent)
        target = self.store.archive_agent(agent, STAMP)
        self.assertTrue((target / "meta.json").is_file())
        self.assertFalse((self.base / "agents" / "1").exists())
        self.assertEqual(self.store.ids(), [])
        again = self.store.create(self.cfg, {"name": "b", "template": "t"},
                                  STAMP)
        self.assertEqual(again.id, "2")

    def test_corrupt_meta_raises(self) -> None:
        folder = self.base / "agents" / "3"
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(BadStore):
            self.store.load("3")

    def test_missing_agent_raises(self) -> None:
        with self.assertRaises(BadStore):
            self.store.load("9")


if __name__ == "__main__":
    unittest.main()
