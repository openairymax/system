# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""八面板组装测试：顺序、缺源降级与缓冲上限。"""

import tempfile
import unittest
from pathlib import Path

import station_support as support

from devstation import config, panel
from devstation.model import PANELS, Panel


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        support.make(self.root)
        self.cfg = config.load(str(self.root / "none.conf"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_eight_sections(self):
        snap = panel.build_snap(self.root, "view", self.cfg)
        self.assertEqual(set(snap.sections), {item.value for item in PANELS})
        self.assertTrue(snap.node)
        self.assertGreater(snap.stamp, 0)
        for item in PANELS:
            self.assertEqual(snap.section(item.value).status, "ok")

    def test_build_one(self):
        snap = panel.build_one(self.root, "backup", "operate", self.cfg)
        self.assertEqual(list(snap.sections), ["backup"])
        self.assertTrue(snap.sections["backup"].rows)
        self.assertFalse(snap.section("logs").ready)

    def test_log_limit(self):
        cfg = dict(self.cfg)
        cfg["devstation.log_buffer_lines"] = 1
        snap = panel.build_snap(self.root, "view", cfg)
        self.assertEqual(len(snap.sections["logs"].rows), 1)

    def test_alert_limit(self):
        cfg = dict(self.cfg)
        cfg["devstation.alert_buffer_size"] = 1
        snap = panel.build_snap(self.root, "view", cfg)
        self.assertEqual(len(snap.sections["alerts"].rows), 1)

    def test_node_fallback(self):
        self.assertTrue(panel.node_name())


class MissingTest(unittest.TestCase):
    def test_empty_root_all_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config.load(str(Path(tmp) / "none.conf"))
            snap = panel.build_snap(tmp, "view", cfg)
            for item in PANELS:
                with self.subTest(panel=item.value):
                    self.assertFalse(snap.section(item.value).ready)

    def test_unknown_panel_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config.load(str(Path(tmp) / "none.conf"))
            snap = panel.build_one(tmp, Panel.MONITOR, "view", cfg)
            self.assertFalse(snap.section("alert").ready)


if __name__ == "__main__":
    unittest.main()
