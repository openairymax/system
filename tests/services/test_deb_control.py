# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# control 单测
#
# 覆盖 deb822 的字段名大小写不敏感、续行拼接、首段截止与依赖拆分。

"""Tests for the airydeb control parser."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airydeb import control  # noqa: E402

SAMPLE = """Package: Demo
Version: 1.0-1
Architecture: amd64
Depends: libc6 (>= 2.34), zlib1g,
 libssl3
Description: demo package
 a longer line
 continues here

Package: second
Version: 9
"""


class ParseTest(unittest.TestCase):
    def test_fields(self):
        fields = control.parse(SAMPLE)
        self.assertEqual(control.field(fields, "package"), "Demo")
        self.assertEqual(control.field(fields, "PACKAGE"), "Demo")

    def test_continuation(self):
        fields = control.parse(SAMPLE)
        self.assertIn("libssl3", control.field(fields, "depends"))
        self.assertIn("continues here", control.field(fields, "description"))

    def test_stops_at_blank(self):
        fields = control.parse(SAMPLE)
        self.assertEqual(control.field(fields, "version"), "1.0-1")

    def test_missing(self):
        self.assertEqual(control.field(control.parse(SAMPLE), "nope"), "")

    def test_items(self):
        fields = control.parse(SAMPLE)
        self.assertEqual(control.items(fields, "depends"),
                         ("libc6 (>= 2.34)", "zlib1g", "libssl3"))

    def test_items_empty(self):
        self.assertEqual(control.items(control.parse(SAMPLE), "recommends"), ())


if __name__ == "__main__":
    unittest.main()
