# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""权限判定测试：单一排序表、--yes 二次确认与拒绝留痕。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from devstation import audit, perm
from devstation.model import BadInput, BadPerm, need_perm


class DenyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logdir = Path(self.tmp.name) / "logs"
        self.env = mock.patch.dict(os.environ, {
            audit.ENV: str(self.logdir),
            audit.OPERATOR: "tester",
        }, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_matrix(self):
        need_perm("operate", "agent.start")
        need_perm("admin", "memory.erase")
        with self.assertRaises(BadPerm):
            need_perm("view", "agent.start")
        with self.assertRaises(BadPerm):
            need_perm("operate", "memory.erase")

    def test_view_denied_and_logged(self):
        with self.assertRaises(BadPerm) as caught:
            perm.authorize("view", "agent.start", target="7")
        self.assertEqual(caught.exception.code, 4)
        rows = audit.read()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"], "denied")
        self.assertEqual(rows[0]["target"], "7")
        self.assertEqual(rows[0]["operator"], "tester")

    def test_admin_needs_yes(self):
        with self.assertRaises(BadPerm) as caught:
            perm.authorize("admin", "memory.erase", target="1")
        self.assertIn("--yes", str(caught.exception))
        self.assertEqual(audit.read()[-1]["result"], "denied")

    def test_admin_with_yes(self):
        before = len(audit.read())
        self.assertTrue(perm.authorize("admin", "memory.erase", yes=True,
                                       target="1"))
        self.assertEqual(len(audit.read()), before)

    def test_operate_passes(self):
        self.assertTrue(perm.authorize("operate", "agent.restart",
                                       target="2"))

    def test_unknown_action(self):
        with self.assertRaises(BadInput):
            perm.authorize("admin", "agent.teleport")
        self.assertEqual(audit.read(), [])


class AuditTest(unittest.TestCase):
    def test_append_and_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {
                audit.ENV: tmp, audit.OPERATOR: "gate"}, clear=False):
                audit.record("view", "snapshot", "", "ok", "test")
                rows = audit.read(1)
                self.assertEqual(rows[0]["action"], "snapshot")
                self.assertEqual(rows[0]["level"], "view")

    def test_read_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {audit.ENV: tmp}):
                self.assertEqual(audit.read(), [])


if __name__ == "__main__":
    unittest.main()
