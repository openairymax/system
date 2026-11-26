# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 记忆迁移与克隆测试
#
# 覆盖项级移动、克隆保留源、跨层搬运、敏感项拒绝、跨属主需要显式同意、
# 同名冲突与源目标同一容器等边界。

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))
sys.path.insert(0, str(ROOT / "services"))

from memory import transfer  # noqa: E402
from memory.model import BadInput, BadPerm, BadState, BadStore, Item  # noqa: E402
from memory.store import Store  # noqa: E402


class TransferTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = Store(str(self.base / "mem"), 4)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def seed(self, agent: str = "42", level: str = "L3") -> None:
        self.store.save_items(level, agent, [
            Item("keep", b"a" * 20),
            Item("move1", b"b" * 30),
            Item("move2", b"c" * 40),
            Item("secret", b"s" * 10, True),
        ], owner="team")

    def test_clone_keeps_source(self) -> None:
        self.seed()
        doc = transfer.clone(self.store, {"from": "42", "to": "43",
                                          "items": "move1,move2"})
        self.assertEqual(doc["mode"], "clone")
        self.assertEqual(doc["items"], 2)
        self.assertEqual(sorted(item.id for item in
                                self.store.items("L3", "42")),
                         ["keep", "move1", "move2", "secret"])
        self.assertEqual(sorted(item.id for item in
                                self.store.items("L3", "43")),
                         ["move1", "move2"])

    def test_move_removes_from_source(self) -> None:
        self.seed()
        doc = transfer.move(self.store, {"from": "42", "to": "43",
                                         "items": "move1"})
        self.assertEqual(doc["mode"], "move")
        self.assertEqual(doc["bytes"], 30)
        self.assertNotIn("move1", [item.id for item in
                                   self.store.items("L3", "42")])
        self.assertIn("move1", [item.id for item in
                                self.store.items("L3", "43")])
        self.assertEqual(self.store.verify("L3", "42"), "ok")
        self.assertEqual(self.store.verify("L3", "43"), "ok")

    def test_move_whole_level(self) -> None:
        self.store.save_items("L3", "45", [Item("a", b"1"), Item("b", b"2")],
                              owner="team")
        doc = transfer.move(self.store, {"from": "45", "to": "46",
                                         "level": "L3"})
        self.assertEqual(doc["items"], 2)
        self.assertEqual(self.store.items("L3", "45"), [])
        self.assertEqual(len(self.store.items("L3", "46")), 2)

    def test_whole_level_rejects_sensitive(self) -> None:
        self.seed()
        with self.assertRaises(BadState):
            transfer.move(self.store, {"from": "42", "to": "44",
                                       "level": "L3"})
        self.assertEqual(len(self.store.items("L3", "42")), 4)

    def test_cross_level_move(self) -> None:
        self.seed()
        doc = transfer.move(self.store, {"from": "42", "to": "42",
                                         "level": "L3", "to_level": "L4",
                                         "items": "keep"})
        self.assertEqual((doc["from_level"], doc["to_level"]), ("L3", "L4"))
        self.assertEqual([item.id for item in self.store.items("L4", "42")],
                         ["keep"])
        self.assertNotIn("keep", [item.id for item in
                                  self.store.items("L3", "42")])

    def test_sensitive_item_rejected(self) -> None:
        self.seed()
        with self.assertRaises(BadState) as caught:
            transfer.move(self.store, {"from": "42", "to": "43",
                                       "items": "secret"})
        self.assertEqual(caught.exception.code, 3)
        self.assertIn("secret", str(caught.exception))

    def test_cross_owner_needs_consent(self) -> None:
        self.seed()
        self.store.save_items("L3", "43", [Item("old", b"o")], owner="other")
        with self.assertRaises(BadPerm) as caught:
            transfer.move(self.store, {"from": "42", "to": "43",
                                       "items": "move1"})
        self.assertEqual(caught.exception.code, 4)
        doc = transfer.move(self.store, {"from": "42", "to": "43",
                                         "items": "move1", "consent": True})
        self.assertEqual(doc["items"], 1)
        self.assertIn("move1", [item.id for item in
                                self.store.items("L3", "43")])

    def test_name_clash_rejected(self) -> None:
        self.seed()
        self.store.save_items("L3", "43", [Item("move1", b"x")], owner="team")
        with self.assertRaises(BadState):
            transfer.clone(self.store, {"from": "42", "to": "43",
                                        "items": "move1"})

    def test_missing_item_rejected(self) -> None:
        self.seed()
        with self.assertRaises(BadState):
            transfer.move(self.store, {"from": "42", "to": "43",
                                       "items": "absent"})

    def test_missing_source_rejected(self) -> None:
        with self.assertRaises(BadStore):
            transfer.move(self.store, {"from": "42", "to": "43"})

    def test_same_container_rejected(self) -> None:
        self.seed()
        with self.assertRaises(BadInput):
            transfer.move(self.store, {"from": "42", "to": "42"})
        with self.assertRaises(BadInput):
            transfer.move(self.store, {"from": "42", "to": "43",
                                       "level": "L2"})

    def test_bad_ids_rejected(self) -> None:
        with self.assertRaises(BadInput):
            transfer.move(self.store, {"from": "../42", "to": "43"})
        with self.assertRaises(BadInput):
            transfer.move(self.store, {"from": "42", "to": "0"})


if __name__ == "__main__":
    unittest.main()
