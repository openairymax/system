# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 性能上限的档位、滞回与落地
#
# 三个层次各自独立验证：of() 只做严重度到阶位的折算，decide() 只在输入与
# 状态之间做滞回，guard 把结论真正写进 pstate。落地侧还要证明两件事——
# 没有 intel_pstate 的机器不能因此被记成失败，以及只读评估不写滞回状态。

"""Tests for the processor ceiling."""

from __future__ import annotations

import unittest
from pathlib import Path

from power_fixture import Tree

from airymax_power import ceiling, guard, tuning
from airymax_power.thermal import Severity


class LadderTest(unittest.TestCase):
    """严重度到阶位的折算。"""

    def test_levels(self) -> None:
        self.assertEqual(ceiling.of(Severity.OK), ceiling.Ceiling(3, 100, 1))
        self.assertEqual(ceiling.of(Severity.WARN), ceiling.Ceiling(2, 90, 1))
        self.assertEqual(ceiling.of(Severity.HOT), ceiling.Ceiling(1, 70, 0))
        self.assertEqual(ceiling.of(Severity.CRITICAL),
                         ceiling.Ceiling(0, 50, 0))

    def test_unknown_holds_full(self) -> None:
        """没有温度读数时不能凭空压上限。"""
        self.assertEqual(ceiling.of(Severity.UNKNOWN), ceiling.Ceiling(3, 100, 1))

    def test_battery_shifts_one_rung(self) -> None:
        self.assertEqual(ceiling.of(Severity.OK, True), ceiling.Ceiling(2, 90, 1))
        self.assertEqual(ceiling.of(Severity.WARN, True),
                         ceiling.Ceiling(1, 70, 0))

    def test_battery_floor(self) -> None:
        """已在最紧一阶时压不动，不能越界。"""
        self.assertEqual(ceiling.of(Severity.CRITICAL, True),
                         ceiling.Ceiling(0, 50, 0))


class HysteresisTest(Tree):
    """滞回：降阶立即、升阶计数。"""

    def test_first_evaluation(self) -> None:
        self.assertEqual(ceiling.decide(self.policy(), ceiling.of(Severity.HOT)),
                         ceiling.Ceiling(1, 70, 0))

    def test_tighten_at_once(self) -> None:
        self.ceiling(3)
        self.assertEqual(ceiling.decide(self.policy(), ceiling.of(Severity.HOT)),
                         ceiling.Ceiling(1, 70, 0))

    def test_relax_waits(self) -> None:
        self.ceiling(1)
        found = ceiling.of(Severity.OK)
        for strike in range(1, ceiling.RECOVER):
            self.assertEqual(ceiling.decide(self.policy(), found),
                             ceiling.Ceiling(1, 70, 0, strike))
            ceiling.save(self.policy().ceil, ceiling.Ceiling(1, 70, 0, strike))
        self.assertEqual(ceiling.decide(self.policy(), found), found)

    def test_relax_resets(self) -> None:
        """中途收紧一次，之前攒下的宽松次数作废。"""
        self.ceiling(1, ceiling.RECOVER - 1)
        self.assertEqual(ceiling.decide(self.policy(), ceiling.of(Severity.HOT)),
                         ceiling.Ceiling(1, 70, 0, 0))

    def test_damaged_state(self) -> None:
        self.base.joinpath("ceiling").write_text("x y\n", "ascii")
        self.assertEqual(ceiling.decide(self.policy(), ceiling.of(Severity.OK)),
                         ceiling.Ceiling(3, 100, 1))

    def test_round_trip(self) -> None:
        found = ceiling.Ceiling(2, 90, 1, 1)
        ceiling.save(self.policy().ceil, found)
        self.assertEqual(ceiling.load(self.policy().ceil), found)


class LandingTest(Tree):
    """把结论写进 intel_pstate 并回读。"""

    def test_hot_writes_cap(self) -> None:
        self.pstate()
        self.zone("SEN1", 75000, critical=80000)
        found = guard.apply(self.policy())
        self.assertEqual(found.ceiling.cap, 70)
        self.assertEqual(_read(self.base / "pstate" / "max_perf_pct"), "70")
        self.assertEqual(_read(self.base / "pstate" / "no_turbo"), "1")

    def test_restores_full(self) -> None:
        self.pstate(cap=70, turbo=0)
        self.zone("SEN1", 45000, critical=80000)
        found = guard.apply(self.policy())
        self.assertEqual(found.ceiling.cap, 100)
        self.assertEqual(_read(self.base / "pstate" / "max_perf_pct"), "100")
        self.assertEqual(_read(self.base / "pstate" / "no_turbo"), "0")

    def test_battery_caps(self) -> None:
        self.pstate()
        self.battery(capacity=80)
        found = guard.apply(self.policy())
        self.assertEqual(found.ceiling.cap, 90)
        self.assertEqual(_read(self.base / "pstate" / "max_perf_pct"), "90")

    def test_absent_is_skipped(self) -> None:
        """没有 intel_pstate 的机器照常判定，既不压上限也不记失败。"""
        found = guard.apply(self.policy())
        self.assertEqual(found.ceiling.cap, 100)
        self.assertNotEqual(found.state, tuning.State.FAILED)
        named = {knob.name: knob for knob in found.knobs}
        self.assertFalse(named[tuning.CAP].present)
        self.assertFalse(named[tuning.NO_TURBO].present)

    def test_inspect_keeps_state(self) -> None:
        self.pstate()
        self.zone("SEN1", 75000, critical=80000)
        found = guard.inspect(self.policy())
        self.assertEqual(found.ceiling.cap, 70)
        self.assertFalse(self.base.joinpath("ceiling").exists())

    def test_apply_saves_state(self) -> None:
        self.pstate()
        self.zone("SEN1", 75000, critical=80000)
        guard.apply(self.policy())
        self.assertEqual(self.base.joinpath("ceiling").read_text("ascii"),
                         "1 0\n")

    def test_stale_cap_miss(self) -> None:
        """节点存在但对不上结论时计入未落地，而不是悄悄消失。"""
        self.pstate(cap=100)
        self.zone("SEN1", 75000, critical=80000)
        knobs = tuning.inspect(self.policy(), self.policy().ac,
                               ceiling.Ceiling(1, 70, 0))
        cap = next(knob for knob in knobs if knob.name == tuning.CAP)
        self.assertTrue(cap.present)
        self.assertFalse(cap.landed)


class SupplyTest(Tree):
    """供电侧的一阶偏移只在确认时生效。"""

    def test_unknown_supply_holds(self) -> None:
        """读不到 power_supply 不等于在用电池。"""
        self.unplug()
        self.pstate()
        self.assertEqual(guard.inspect(self.policy()).ceiling.cap, 100)

    def test_weak_supply_tightens(self) -> None:
        self.pstate()
        self.both(status="Discharging", capacity=80)
        self.assertEqual(guard.inspect(self.policy()).ceiling.cap, 90)


def _read(path: Path) -> str | None:
    return path.read_text("ascii").strip() if path.exists() else None


if __name__ == "__main__":
    unittest.main()
