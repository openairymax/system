# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""确定性帧渲染测试：宽高不越界、可复现、缺源可见、过滤生效。"""

import tempfile
import unittest
from pathlib import Path

import station_support as support

from devstation import config, panel, render
from devstation.model import Panel


class FrameTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        support.make(self.root)
        self.cfg = config.load(str(self.root / "none.conf"))
        self.snap = panel.build_snap(self.root, "view", self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def test_size_exact(self):
        for width, height in ((100, 30), (60, 12), (20, 3), (8, 4)):
            with self.subTest(width=width, height=height):
                text = render.frame(self.snap, Panel.MONITOR, width, height)
                lines = text.splitlines()
                self.assertEqual(len(lines), height)
                self.assertTrue(all(len(line) <= max(width, 8)
                                    for line in lines))

    def test_deterministic(self):
        first = render.frame(self.snap, Panel.LOGS, 80, 20)
        second = render.frame(self.snap, Panel.LOGS, 80, 20)
        self.assertEqual(first, second)

    def test_content(self):
        text = render.frame(self.snap, Panel.MONITOR, 100, 30)
        self.assertIn("airymaxmon", text)
        self.assertIn("panel=监控", text)

    def test_filter(self):
        text = render.frame(self.snap, Panel.AGENTS, 100, 30, "alpha")
        self.assertIn("alpha", text)
        self.assertNotIn("panel=日志", text)
        blank = render.frame(self.snap, Panel.AGENTS, 100, 30, "no-such-row")
        self.assertIn("(空)", blank)

    def test_lines(self):
        sec = self.snap.section("config")
        text = render.lines(sec, "config", 120)
        self.assertTrue(text.startswith("config status=ok"))
        self.assertIn("agent.max_instances", text)


class MissingTest(unittest.TestCase):
    def test_unavailable_reason_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config.load(str(Path(tmp) / "none.conf"))
            snap = panel.build_snap(tmp, "view", cfg)
            text = render.frame(snap, Panel.BACKUP, 80, 10)
            self.assertIn("unavailable", text)
            self.assertEqual(len(text.splitlines()), 10)
            self.assertTrue(render.lines(snap.section("backup"), "backup")
                            .startswith("backup status=unavailable reason="))


if __name__ == "__main__":
    unittest.main()
