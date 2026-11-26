# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 安全擦除测试
#
# 覆盖 quick/overwrite/secure 三种级别、覆写函数逐字节证据、部分擦除保留
# 同块其他项、crypto 在落盘前显式拒绝、审计日志与状态文件。

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))
sys.path.insert(0, str(ROOT / "services"))

from memory import audit, config, erase  # noqa: E402
from memory.model import (  # noqa: E402
    BadInput,
    BadState,
    BadStore,
    BadUnsupported,
    Item,
    Method,
)
from memory.store import Store  # noqa: E402


class EraseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.logs = self.base / "logs"
        self.env = mock.patch.dict(os.environ, {
            "AIRY_MEMORY_LOGDIR": str(self.logs),
            "AIRY_MEMORY_OPERATOR": "auditor",
        })
        self.env.start()
        self.store = Store(str(self.base / "mem"), 4)
        self.cfg = config.load(str(self.base / "absent.conf"))

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def seed(self, agent: str = "5", count: int = 8) -> None:
        self.store.save_items("L3", agent, [
            Item(f"m{n}", b"p" * 60, sensitive=(n == 3))
            for n in range(1, count + 1)])

    def test_overwrite_file_zero_and_one(self) -> None:
        blob = self.base / "blob.bin"
        blob.write_bytes(b"\xaa" * 300)
        written = erase.overwrite_file(blob, [erase.zero_fill, erase.one_fill])
        self.assertEqual(written, 600)
        self.assertEqual(blob.read_bytes(), b"\xff" * 300)

    def test_random_fill_is_random(self) -> None:
        first = erase.random_fill(64)
        second = erase.random_fill(64)
        self.assertEqual(len(first), 64)
        self.assertNotEqual(first, second)
        self.assertEqual(erase.zero_fill(3), b"\x00\x00\x00")
        self.assertEqual(erase.one_fill(2), b"\xff\xff")

    def test_fill_plan_matches_level(self) -> None:
        self.assertEqual(erase._fills(Method.QUICK), [])
        self.assertEqual(len(erase._fills(Method.OVERWRITE)), 1)
        self.assertEqual(len(erase._fills(Method.SECURE)), 3)

    def test_overwrite_file_rejects_empty_fill(self) -> None:
        blob = self.base / "blob.bin"
        blob.write_bytes(b"x")
        with self.assertRaises(BadInput):
            erase.overwrite_file(blob, [])
        with self.assertRaises(BadStore):
            erase.overwrite_file(self.base / "absent.bin", [erase.zero_fill])

    def test_quick_whole_drops_container(self) -> None:
        self.seed()
        row = erase.erase(self.store, self.cfg,
                          {"agent": "5", "all": True, "method": "quick"})
        self.assertEqual(row["method"], "quick")
        self.assertEqual(row["passes"], 0)
        self.assertEqual(row["scope"], "agent")
        self.assertFalse(self.store.exists("L3", "5"))
        self.assertFalse((self.base / "mem" / "l3" / "5").exists())

    def test_quick_partial_keeps_others(self) -> None:
        self.seed()
        row = erase.erase(self.store, self.cfg, {
            "agent": "5", "items": "m1,m2", "method": "quick"})
        self.assertEqual(row["items"], 2)
        self.assertEqual(sorted(item.id for item in
                                self.store.items("L3", "5")),
                         [f"m{n}" for n in range(3, 9)])
        self.assertEqual(self.store.verify("L3", "5"), "ok")

    def test_overwrite_partial_preserves_block_peers(self) -> None:
        self.seed()
        row = erase.erase(self.store, self.cfg, {
            "agent": "5", "items": "m1", "method": "overwrite"})
        self.assertEqual(row["passes"], 1)
        self.assertGreater(row["bytes"], 0)
        self.assertEqual(sorted(item.id for item in
                                self.store.items("L3", "5")),
                         [f"m{n}" for n in range(2, 9)])

    def test_secure_whole_writes_three_passes(self) -> None:
        self.seed()
        row = erase.erase(self.store, self.cfg,
                          {"agent": "5", "all": True, "method": "secure"})
        self.assertEqual(row["passes"], 3)
        self.assertGreater(row["bytes"], 0)
        self.assertFalse(self.store.exists("L3", "5"))

    def test_secure_partial_rejects_missing_item(self) -> None:
        self.seed()
        with self.assertRaises(BadState):
            erase.erase(self.store, self.cfg, {
                "agent": "5", "items": "absent", "method": "secure"})
        with self.assertRaises(BadInput):
            erase.erase(self.store, self.cfg,
                        {"agent": "5", "method": "secure"})

    def test_crypto_rejected_before_disk_change(self) -> None:
        self.seed()
        with self.assertRaises(BadUnsupported) as caught:
            erase.erase(self.store, self.cfg,
                        {"agent": "5", "all": True, "level": "L4",
                         "method": "crypto"})
        self.assertEqual(caught.exception.code, 3)
        self.assertEqual(len(self.store.items("L3", "5")), 8)
        self.assertFalse(self.store.exists("L4", "5"))

    def test_audit_and_state_written(self) -> None:
        self.seed()
        erase.erase(self.store, self.cfg,
                    {"agent": "5", "all": True, "method": "secure"})
        path = self.logs / audit.FILE
        self.assertTrue(path.is_file())
        rows = [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["operator"], "auditor")
        self.assertEqual(row["scope"], "agent")
        self.assertEqual(row["method"], "secure")
        self.assertEqual(row["passes"], 3)
        self.assertEqual(row["level"], "L3")
        self.assertTrue(row["ts"].endswith("Z"))
        state = erase.status(self.store)
        self.assertEqual(state["method"], "secure")
        self.assertEqual(len(erase.trail()), 1)

    def test_status_none_before_erase(self) -> None:
        self.assertEqual(erase.status(self.store), {"state": "none"})
        self.assertEqual(erase.trail(), [])

    def test_audit_can_be_disabled(self) -> None:
        self.seed()
        self.cfg["erase_audit"] = False
        erase.erase(self.store, self.cfg,
                    {"agent": "5", "all": True, "method": "quick"})
        self.assertEqual(erase.trail(), [])


if __name__ == "__main__":
    unittest.main()
