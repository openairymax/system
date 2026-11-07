# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# plan 单测
#
# 维护者脚本的默认拒绝与显式放行、脚本全文渲染、依赖只报告不映射。

"""Tests for the airydeb install policy."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airydeb import plan  # noqa: E402
from airydeb.archive import Script  # noqa: E402

POSTINST = Script("postinst", "#!/bin/sh\nexit 0\n")


class ScriptTest(unittest.TestCase):
    def test_blocks_by_default(self):
        self.assertTrue(plan.blocked((POSTINST,), False))

    def test_allows_when_ignored(self):
        self.assertFalse(plan.blocked((POSTINST,), True))

    def test_no_scripts(self):
        self.assertFalse(plan.blocked((), False))

    def test_renders_name_and_body(self):
        text = plan.render((POSTINST,))
        self.assertIn("postinst", text)
        self.assertIn("exit 0", text)

    def test_renders_nothing(self):
        self.assertEqual(plan.render(()), "")


class DepsTest(unittest.TestCase):
    def test_reports_both_fields(self):
        fields = {
            "depends": "libc6 (>= 2.34), zlib1g",
            "recommends": "curl",
        }
        rows = plan.deps(fields)
        self.assertIn("Depends: zlib1g", rows)
        self.assertIn("Recommends: curl", rows)
        self.assertEqual(len(rows), 3)

    def test_reports_nothing(self):
        self.assertEqual(plan.deps({}), ())


if __name__ == "__main__":
    unittest.main()
