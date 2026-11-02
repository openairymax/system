# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# RT 带宽读取回归测试
#
# 用临时文件充当内核接口，覆盖可读、不限（-1）、空文件、非整数与缺失
# 五种情形，并验证周期无效时整体不可判定。不读取本机 /proc。

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airymax_rt import policy  # noqa: E402
from airymax_rt import sysctl  # noqa: E402


class ReadTest(unittest.TestCase):
    """单个接口的解析口径。"""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name)

    def write(self, name: str, text: str) -> str:
        path = self.base / name
        path.write_text(text, encoding="ascii")
        return str(path)

    def test_value(self) -> None:
        path = self.write("runtime", "950000\n")
        self.assertEqual(sysctl.read_value(path), 950000)

    def test_unlimited(self) -> None:
        path = self.write("runtime", "-1\n")
        self.assertEqual(sysctl.read_value(path), -1)

    def test_empty(self) -> None:
        path = self.write("runtime", "  \n")
        self.assertIsNone(sysctl.read_value(path))

    def test_not_integer(self) -> None:
        path = self.write("runtime", "unlimited\n")
        self.assertIsNone(sysctl.read_value(path))

    def test_missing(self) -> None:
        self.assertIsNone(sysctl.read_value(str(self.base / "absent")))


class BudgetTest(unittest.TestCase):
    """两份接口合成一个预算；周期无效时拒绝给出预算。"""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name)

    def policy(self, total: str = "950000\n", period: str = "1000000\n"):
        total_path = self.base / "runtime"
        period_path = self.base / "period"
        total_path.write_text(total, encoding="ascii")
        period_path.write_text(period, encoding="ascii")
        return policy.Policy(
            total_path=str(total_path), period_path=str(period_path)
        )

    def test_design_values(self) -> None:
        found = sysctl.budget(self.policy())
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.total, 950000)
        self.assertEqual(found.period, 1000000)
        self.assertTrue(found.limited)

    def test_unlimited_is_not_limited(self) -> None:
        found = sysctl.budget(self.policy(total="-1\n"))
        assert found is not None
        self.assertEqual(found.total, -1)
        self.assertFalse(found.limited)

    def test_zero_period(self) -> None:
        self.assertIsNone(sysctl.budget(self.policy(period="0\n")))

    def test_missing_total(self) -> None:
        found = self.policy()
        found = policy.Policy(
            total_path=str(self.base / "absent"),
            period_path=found.period_path,
        )
        self.assertIsNone(sysctl.budget(found))


if __name__ == "__main__":
    unittest.main()
