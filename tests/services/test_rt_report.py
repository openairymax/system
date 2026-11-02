# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# RT 带宽判定与落地回归测试
#
# 覆盖七个判定 token 的触发条件，并验证 apply 之后的状态由回读决定，
# 而不是由写入返回值决定。全部使用临时目录模拟内核接口与 cgroup 树。

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from airymax_rt import bandwidth, cgroup, policy, report  # noqa: E402
from rt_fixture import Tree  # noqa: E402


class InspectTest(Tree):
    """只读判定不得修改任何文件。"""

    def test_ok(self) -> None:
        self.all_slices()
        found = bandwidth.inspect(self.policy())
        self.assertIs(found.state, report.State.OK)
        self.assertTrue(found.ok)
        self.assertEqual(found.applied, 4)
        self.assertEqual(found.missing, ())
        self.assertIs(found.kind, cgroup.Kind.V1)

    def test_partial(self) -> None:
        mount = self.mount()
        self.slice(mount, "system.slice", 190000)
        self.slice(mount, "user.slice", 190000)
        found = bandwidth.inspect(self.policy())
        self.assertIs(found.state, report.State.PARTIAL)
        self.assertEqual(found.applied, 2)
        self.assertEqual(
            found.missing, ("airymax.slice", "airymax-desktop.slice")
        )

    def test_failed(self) -> None:
        self.mount()
        found = bandwidth.inspect(self.policy())
        self.assertIs(found.state, report.State.FAILED)
        self.assertEqual(found.applied, 0)

    def test_disabled(self) -> None:
        self.all_slices()
        found = bandwidth.inspect(self.policy(total="-1\n"))
        self.assertIs(found.state, report.State.DISABLED)
        self.assertEqual(found.total, -1)
        self.assertEqual(found.wanted, 0)

    def test_no_cpu_when_budget_missing(self) -> None:
        found = bandwidth.inspect(self.policy())
        self.assertIs(found.state, report.State.NO_CGROUP_V1)
        missing = policy.Policy(
            total_path=str(self.base / "absent"),
            period_path=str(self.base / "period"),
            cgroup_root=str(self.base),
        )
        self.assertIs(bandwidth.inspect(missing).state, report.State.NO_CPU)

    def test_no_cgroup_v1(self) -> None:
        found = bandwidth.inspect(self.policy())
        self.assertIs(found.state, report.State.NO_CGROUP_V1)

    def test_unaccounted(self) -> None:
        (self.base / cgroup.CONTROLLER).write_text("cpu memory\n", "ascii")
        found = bandwidth.inspect(self.policy())
        self.assertIs(found.state, report.State.UNACCOUNTED)

    def test_no_cpu_on_unified_tree(self) -> None:
        (self.base / cgroup.CONTROLLER).write_text("io memory\n", "ascii")
        found = bandwidth.inspect(self.policy())
        self.assertIs(found.state, report.State.NO_CPU)

    def test_dump_shape(self) -> None:
        found = bandwidth.inspect(self.policy())
        payload = found.dump()
        self.assertEqual(payload["state"], "no-cgroup-v1")
        self.assertEqual(payload["kind"], "")
        self.assertEqual(payload["wanted"], 4)
        self.assertEqual(payload["applied"], 0)
        self.assertEqual(len(payload["missing"]), 4)
        self.assertEqual(payload["slices"], [
            {"slice": name, "budget": 190000, "applied": False}
            for name in policy.DEFAULT_SLICES
        ])

    def test_dump_lists_slices(self) -> None:
        self.all_slices()
        payload = bandwidth.inspect(self.policy()).dump()
        self.assertEqual(payload["kind"], "v1")
        self.assertEqual(payload["wanted"], 4)
        self.assertEqual(payload["applied"], 4)
        self.assertEqual(payload["missing"], [])
        self.assertEqual(payload["slices"][0]["budget"], 190000)


class ApplyTest(Tree):
    """落地后由回读决定结论。"""

    def test_creates_missing_slices(self) -> None:
        mount = self.mount()
        found = bandwidth.apply(self.policy())
        self.assertIs(found.state, report.State.OK)
        for name in policy.DEFAULT_SLICES:
            text = (mount / name / cgroup.V1_FILE).read_text(encoding="ascii")
            self.assertEqual(text.strip(), "190000")

    def test_repairs_partial(self) -> None:
        mount = self.mount()
        self.slice(mount, "system.slice", 190000)
        found = bandwidth.apply(self.policy())
        self.assertIs(found.state, report.State.OK)

    def test_disabled_writes_nothing(self) -> None:
        mount = self.mount()
        found = bandwidth.apply(self.policy(total="-1\n"))
        self.assertIs(found.state, report.State.DISABLED)
        self.assertEqual(
            sorted(item.name for item in mount.iterdir()), [cgroup.V1_FILE]
        )

    def test_unaccounted_writes_nothing(self) -> None:
        (self.base / cgroup.CONTROLLER).write_text("cpu memory\n", "ascii")
        found = bandwidth.apply(self.policy())
        self.assertIs(found.state, report.State.UNACCOUNTED)
        self.assertFalse((self.base / policy.DEFAULT_SLICES[0]).exists())


if __name__ == "__main__":
    unittest.main()
