# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# RT 带宽策略与切分计划回归测试
#
# 只验证纯计算：默认策略是否与设计文档一致、每份预算如何取整、总预算
# 不可切分时是否拒绝产生计划。不读取本机 /proc 与 cgroup。

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airymax_rt import policy  # noqa: E402
from airymax_rt import allocation  # noqa: E402


class DefaultTest(unittest.TestCase):
    """默认值与设计文档的 950000 总预算、5 份切分逐字对齐。"""

    def test_slices(self) -> None:
        self.assertEqual(
            policy.Policy().slices,
            (
                "system.slice",
                "user.slice",
                "airymax.slice",
                "airymax-desktop.slice",
            ),
        )

    def test_divisor(self) -> None:
        self.assertEqual(policy.Policy().divisor, 5)

    def test_paths(self) -> None:
        found = policy.Policy()
        self.assertEqual(
            found.total_path, "/proc/sys/kernel/sched_rt_runtime_us"
        )
        self.assertEqual(
            found.period_path, "/proc/sys/kernel/sched_rt_period_us"
        )
        self.assertEqual(found.cgroup_root, "/sys/fs/cgroup")

    def test_wanted(self) -> None:
        self.assertEqual(policy.Policy().wanted, 4)

    def test_mark_is_single_line_token(self) -> None:
        self.assertNotIn(" ", policy.MARK)
        self.assertEqual(policy.MARK, "AIRYMAX-RT-CAP")


class ShareTest(unittest.TestCase):
    """向下取整，且只在总预算为正时给出份额。"""

    def test_design_budget(self) -> None:
        self.assertEqual(policy.Policy().share(950000), 190000)

    def test_rounds_down(self) -> None:
        self.assertEqual(policy.Policy().share(950001), 190000)

    def test_zero_and_negative(self) -> None:
        self.assertEqual(policy.Policy().share(0), 0)
        self.assertEqual(policy.Policy().share(-1), 0)

    def test_invalid_divisor(self) -> None:
        found = policy.Policy(divisor=0)
        self.assertEqual(found.share(950000), 0)

    def test_single_share(self) -> None:
        found = policy.Policy(divisor=1)
        self.assertEqual(found.share(950000), 950000)


class PlanTest(unittest.TestCase):
    """计划条目与 slice 顺序一致，预算全部等于同一份。"""

    def test_design_budget(self) -> None:
        entries = allocation.allocate(policy.Policy(), 950000)
        self.assertEqual(len(entries), 4)
        self.assertEqual(
            [item.slice for item in entries],
            list(policy.DEFAULT_SLICES),
        )
        self.assertTrue(all(item.budget == 190000 for item in entries))

    def test_disabled_when_total_not_positive(self) -> None:
        self.assertEqual(allocation.allocate(policy.Policy(), 0), ())
        self.assertEqual(allocation.allocate(policy.Policy(), -1), ())

    def test_empty_when_share_rounds_to_zero(self) -> None:
        self.assertEqual(allocation.allocate(policy.Policy(), 4), ())

    def test_custom_slices(self) -> None:
        found = policy.Policy(slices=("user.slice",), divisor=2)
        entries = allocation.allocate(found, 1000)
        self.assertEqual(entries, (allocation.Entry("user.slice", 500),))


if __name__ == "__main__":
    unittest.main()
