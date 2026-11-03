# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 空闲深度控制回归测试
#
# 空闲深度是本轮 285H 现场定位到的实时性根因：C3 的退出延迟已经超过软实时
# 上限。用例覆盖五条路径：AC 上按预算关闭过深空闲态、只读评估不写节点、
# 电池上恢复固件默认、过热时恢复固件默认、退出延迟本身在预算内时不动作，
# 以及没有 cpuidle 的机器不生成控制点。

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from airymax_power import guard, idle  # noqa: E402
from airymax_power.report import Report  # noqa: E402
from airymax_power.tuning import Knob, State  # noqa: E402
from power_fixture import Tree  # noqa: E402


class IdleTest(Tree):
    def slot(self, cpu: int, index: int) -> Path:
        return (self.base / "cpus" / f"cpu{cpu}" / "cpuidle"
                / f"state{index}" / "disable")

    def off(self) -> int:
        """统计被禁用的空闲态数量。"""
        paths = self.base.glob("cpus/cpu*/cpuidle/state*/disable")
        return sum(1 for path in paths if path.read_text().strip() == "1")

    def knob(self, found: Report) -> Knob:
        return next(item for item in found.knobs
                    if item.name.endswith("/idle"))

    def test_ac_caps(self) -> None:
        self.idle()
        before = self.knob(guard.inspect(self.policy()))
        self.assertEqual(before.current, "C3_ACPI 1048us")
        found = guard.apply(self.policy())
        self.assertIs(found.state, State.OK)
        self.assertEqual(self.off(), 4, "每个核各关掉一个 C3")
        self.assertEqual(self.slot(0, 3).read_text().strip(), "1")
        self.assertEqual(self.slot(0, 2).read_text().strip(), "0")
        knob = self.knob(found)
        self.assertEqual(knob.target, f"<={idle.BUDGET_US}us")
        self.assertEqual(knob.current, "C2_ACPI 127us")
        self.assertTrue(knob.landed)

    def test_status_reads_only(self) -> None:
        """只读评估发现未落地，但不写任何节点。"""
        self.idle()
        found = guard.inspect(self.policy())
        self.assertEqual(self.off(), 0)
        self.assertFalse(self.knob(found).landed)

    def test_battery_restores(self) -> None:
        self.idle()
        guard.apply(self.policy())
        self.battery()
        found = guard.apply(self.policy())
        self.assertEqual(self.off(), 0)
        knob = self.knob(found)
        self.assertEqual(knob.target, "firmware")
        self.assertTrue(knob.landed)

    def test_hot_restores(self) -> None:
        self.idle()
        guard.apply(self.policy())
        self.zone("x86_pkg_temp", 88000, critical=95000)
        found = guard.apply(self.policy())
        self.assertEqual(self.off(), 0)
        self.assertEqual(self.knob(found).target, "firmware")

    def test_within_budget(self) -> None:
        """最深空闲态本身就在预算内时不需要关闭。"""
        self.idle(depth=500)
        found = guard.apply(self.policy())
        self.assertIs(found.state, State.OK)
        self.assertEqual(self.off(), 0)
        self.assertEqual(self.knob(found).current, "C3_ACPI 500us")

    def test_absent(self) -> None:
        """没有 cpuidle 的机器不生成空闲控制点。"""
        found = guard.apply(self.policy())
        self.assertIs(found.state, State.OK)
        self.assertFalse([k for k in found.knobs if k.name.endswith("/idle")])
        self.assertEqual(idle.inspect(self.policy(), True), ())


if __name__ == "__main__":
    unittest.main()
