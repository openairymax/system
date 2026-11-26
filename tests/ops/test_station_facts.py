# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""只读事实采集的单元测试：真实临时目录，不 mock 采集逻辑。"""

import os
import tempfile
import unittest
from pathlib import Path

import station_support as support

from devstation import facts
from devstation.model import BadState

PANELS = ("monitor", "logs", "agents", "alerts", "incidents", "memory",
          "backup", "config")


def _collect(root) -> dict:
    base = Path(root)
    return {
        "monitor": facts.daemon_rows(base),
        "logs": facts.log_rows(base, 50),
        "agents": facts.agent_rows(base),
        "alerts": facts.alert_rows(base, 50),
        "incidents": facts.incident_rows(base),
        "memory": facts.memory_rows(base),
        "backup": facts.backup_rows(base),
        "config": facts.config_rows(base),
    }


class PopulatedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        support.make(self.root)
        self.data = _collect(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_sources_ok(self):
        for name in PANELS:
            with self.subTest(panel=name):
                self.assertEqual(self.data[name].status, "ok")
                self.assertTrue(self.data[name].rows)

    def test_values(self):
        mon = self.data["monitor"].rows[0]
        self.assertEqual(mon["daemon"], "airymaxmon")
        self.assertEqual(mon["cpu_pct"], 12.5)
        self.assertEqual(mon["health_score"], 98)
        self.assertEqual(self.data["agents"].rows[0]["name"], "alpha")
        self.assertEqual(self.data["alerts"].rows[0]["alert_id"], "A-1")
        self.assertEqual(self.data["incidents"].rows[0]["title"],
                         "daemon down")
        mem = self.data["memory"].rows[0]
        self.assertEqual((mem["agent"], mem["l3_items"]), ("1", 1))
        self.assertEqual(self.data["backup"].rows[0]["backup_id"],
                         "FULL-20261127-001")
        self.assertEqual(self.data["config"].rows[0]["value"], "8")
        self.assertIn("airy-agent",
                      {row["source"] for row in self.data["logs"].rows})

    def test_stores(self):
        store, folder = facts.agent_store(self.root)
        self.assertTrue(folder.is_dir())
        self.assertEqual(len(store.rows()), 1)
        mem, _ = facts.memory_store(self.root)
        self.assertEqual(mem.ids("L3"), ["1"])


class MissingTest(unittest.TestCase):
    def test_empty_root_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = _collect(tmp)
            for name in PANELS:
                with self.subTest(panel=name):
                    sec = data[name]
                    self.assertEqual(sec.status, "unavailable")
                    self.assertTrue(sec.reason)

    def test_store_requires_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BadState):
                facts.agent_store(Path(tmp))
            with self.assertRaises(BadState):
                facts.memory_store(Path(tmp))


class BrokenTest(unittest.TestCase):
    def test_unreadable_config_reported(self):
        if os.geteuid() == 0:
            self.skipTest("root 无视文件权限，无法构造不可读样本")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "etc/agentrt"
            folder.mkdir(parents=True)
            bad = folder / "secret.conf"
            bad.write_text("x = 1\n", encoding="utf-8")
            os.chmod(bad, 0)
            try:
                sec = facts.config_rows(root)
            finally:
                os.chmod(bad, 0o600)
            self.assertEqual(sec.status, "ok")
            self.assertEqual(sec.rows[0]["value"], "权限不足或文件损坏")

    def test_unreadable_logs_unavailable(self):
        if os.geteuid() == 0:
            self.skipTest("root 无视文件权限，无法构造不可读样本")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "var/log/airy"
            folder.mkdir(parents=True)
            bad = folder / "bad.log"
            bad.write_text("INFO x\n", encoding="utf-8")
            os.chmod(bad, 0)
            try:
                sec = facts.log_rows(root, 10)
            finally:
                os.chmod(bad, 0o600)
            self.assertEqual(sec.status, "unavailable")

    def test_bad_json_degrades(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "var/lib/airy/alerts"
            folder.mkdir(parents=True)
            (folder / "active.json").write_text("{not json", encoding="utf-8")
            sec = facts.alert_rows(root, 10)
            self.assertEqual(sec.status, "unavailable")


if __name__ == "__main__":
    unittest.main()
