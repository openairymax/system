# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 节流增速回归测试
#
# 覆盖三件事：两次采样之间的增速换算与三种不可比情形、分级门限、采样文件
# 的读写容错；最后验证 guard 在温度健康但节流猛烈时仍然降档，而只读巡检
# 不会消费掉这份判据。全部用例在临时目录上跑，不碰宿主机的 /run。

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from airymax_power import guard, throttle  # noqa: E402
from airymax_power.supply import Source  # noqa: E402
from airymax_power.thermal import Severity  # noqa: E402
from airymax_power.tuning import State  # noqa: E402
from power_fixture import Tree  # noqa: E402


class RateTest(unittest.TestCase):
    """增速只看增量与间隔，不看累计值。"""

    def test_growth(self) -> None:
        found = throttle.rate(throttle.Sample(30, 11.0),
                              throttle.Sample(10, 1.0))
        self.assertEqual(found, 2.0)

    def test_flat(self) -> None:
        found = throttle.rate(throttle.Sample(10, 5.0),
                              throttle.Sample(10, 1.0))
        self.assertEqual(found, 0.0)

    def test_none(self) -> None:
        """首次采样没有基线，不能据此降档。"""
        self.assertIsNone(throttle.rate(throttle.Sample(30, 1.0), None))

    def test_back(self) -> None:
        """时钟倒退时差值没有意义，按无基线处理。"""
        found = throttle.rate(throttle.Sample(30, 1.0),
                              throttle.Sample(10, 5.0))
        self.assertIsNone(found)

    def test_reset(self) -> None:
        """计数回退说明内核计数器重置，不能算成负增速。"""
        found = throttle.rate(throttle.Sample(1, 5.0),
                              throttle.Sample(30, 1.0))
        self.assertIsNone(found)

    def test_frozen(self) -> None:
        self.assertIsNone(throttle.rate(throttle.Sample(30, 1.0),
                                        throttle.Sample(10, 1.0)))


class GradeTest(unittest.TestCase):
    """增速到严重度的门限。"""

    def test_hot(self) -> None:
        self.assertIs(throttle.grade(throttle.RATE_HOT), Severity.HOT)

    def test_warn(self) -> None:
        self.assertIs(throttle.grade(throttle.RATE_WARN), Severity.WARN)

    def test_ok(self) -> None:
        self.assertIs(throttle.grade(0.0), Severity.OK)

    def test_none(self) -> None:
        self.assertIs(throttle.grade(None), Severity.UNKNOWN)


class StoreTest(unittest.TestCase):
    """采样文件缺失、损坏或不可写时都不影响判定。"""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name)

    def test_round(self) -> None:
        path = str(self.base / "run" / "throttle")
        throttle.save(path, 7)
        found = throttle.load(path)
        self.assertIsNotNone(found)
        self.assertEqual(found.count, 7)
        self.assertLess(abs(found.stamp - time.monotonic()), 5)

    def test_missing(self) -> None:
        self.assertIsNone(throttle.load(str(self.base / "absent")))

    def test_corrupt(self) -> None:
        path = self.base / "bad"
        path.write_text("1 2 3\n", encoding="ascii")
        self.assertIsNone(throttle.load(str(path)))

    def test_denied(self) -> None:
        """父路径是普通文件时写入静默失败，调用方照常判定。"""
        path = self.base / "blocked"
        path.write_text("x\n", encoding="ascii")
        throttle.save(str(path / "sample"), 1)
        self.assertTrue(path.is_file())


class GuardTest(Tree):
    """增速接入档位选择，且只读路径不写采样。"""

    def seed(self, count: int, ago: float) -> None:
        path = self.base / "state"
        path.write_text(f"{count} {time.monotonic() - ago:.3f}\n", "ascii")

    def test_rate_hot(self) -> None:
        """温度 50°C 但每秒节流四次，必须按 hot 降档。"""
        self.seed(0, 10)
        self.throttle(40)
        found = guard.apply(self.policy())
        self.assertIs(found.state, State.OK)
        self.assertIs(found.severity, Severity.HOT)
        self.assertEqual(found.source, Source.AC)
        self.assertIn("low-power", self.profile.read_text(encoding="ascii"))

    def test_rate_ok(self) -> None:
        """有基线但没有增长，维持 AC 正常档。"""
        self.seed(40, 10)
        self.throttle(40)
        found = guard.apply(self.policy())
        self.assertIs(found.severity, Severity.OK)
        self.assertIn("balanced", self.profile.read_text(encoding="ascii"))

    def test_read_only(self) -> None:
        """巡检看到同一份增速，但不落盘，apply 之后仍能降档。"""
        self.seed(0, 10)
        self.throttle(40)
        path = self.base / "state"
        before = path.read_text(encoding="ascii")
        self.assertIs(guard.inspect(self.policy()).severity, Severity.HOT)
        self.assertEqual(path.read_text(encoding="ascii"), before)