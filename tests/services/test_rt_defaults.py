# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# RT 新建组默认带宽与补写的回归测试
#
# 覆盖 RT-01 的三道门禁与补写口径：新建组默认 0（内核不继承父组）、
# 直属组预算非 0、RLIMIT_RTPRIO 为 0 且非 root。补写只写有限值，取父组
# 同额或策略份额，不写 -1；已非 0 的组一律跳过。判定与补写都不依赖宿主
# 内核，临时树里用文件模拟两种记账布局。

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from airymax_rt import cgroup, cli, defaults  # noqa: E402
from rt_fixture import Tree  # noqa: E402

UNLIMITED = "Max realtime priority        unlimited unlimited\n"

ZERO = "Max realtime priority                0         0\n"


def run(argv: list[str]) -> tuple[int, str]:
    """执行一次命令行，返回退出码与标准输出。"""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


class Base(Tree):
    """在共用临时机器上补两件本组用例需要的东西。"""

    def limits(self, text: str) -> str:
        path = self.base / "limits"
        path.write_text(text, encoding="ascii")
        return str(path)

    def layout(self) -> cgroup.Layout:
        found = cgroup.detect(str(self.base))
        assert found is not None
        return found

    def v2(self, budget: str = "950000 1000000") -> None:
        (self.base / cgroup.CONTROLLER).write_text(
            "cpu io memory\n", encoding="ascii"
        )
        (self.base / cgroup.V2_FILE).write_text(
            budget + "\n", encoding="ascii"
        )
        (self.base / cgroup.SUBTREE).write_text("cpu\n", encoding="ascii")


class InspectTest(Base):
    """只读判读的分支覆盖。"""

    def test_new_group_is_blocked(self) -> None:
        self.mount()
        found = defaults.inspect(
            self.policy(), limits=self.limits(UNLIMITED), privileged=True
        )
        self.assertEqual(found.verdict, defaults.BLOCKED)
        self.assertIn(defaults.NEW_GROUP_ZERO, found.blocked)
        self.assertIsNone(found.fresh)
        self.assertTrue(found.enabled)
        self.assertEqual(found.root, "950000")

    def test_seeded_group_is_budgeted(self) -> None:
        mount = self.mount()
        self.slice(mount, "user.slice", 190000)
        found = defaults.inspect(
            self.policy(),
            "user.slice",
            self.limits(UNLIMITED),
            privileged=True,
        )
        self.assertEqual(found.verdict, defaults.BUDGETED)
        self.assertEqual(found.fresh, "190000")
        self.assertFalse(found.blocked)

    def test_zero_group_is_blocked(self) -> None:
        mount = self.mount()
        self.slice(mount, "user.slice", 0)
        found = defaults.inspect(
            self.policy(),
            "user.slice",
            self.limits(UNLIMITED),
            privileged=True,
        )
        self.assertEqual(found.verdict, defaults.BLOCKED)
        self.assertIn(defaults.GROUP_ZERO, found.blocked)

    def test_absent_group(self) -> None:
        self.mount()
        found = defaults.inspect(
            self.policy(),
            "user.slice",
            self.limits(UNLIMITED),
            privileged=True,
        )
        self.assertIn(defaults.GROUP_ABSENT, found.blocked)

    def test_unlimited_global(self) -> None:
        self.mount()
        found = defaults.inspect(
            self.policy(total="-1\n"),
            limits=self.limits(UNLIMITED),
            privileged=True,
        )
        self.assertEqual(found.verdict, defaults.UNLIMITED)
        self.assertEqual(found.total, -1)
        self.assertFalse(found.blocked)

    def test_global_zero_blocks(self) -> None:
        self.mount()
        found = defaults.inspect(
            self.policy(total="0\n"),
            limits=self.limits(UNLIMITED),
            privileged=True,
        )
        self.assertEqual(found.verdict, defaults.BLOCKED)
        self.assertIn(defaults.GLOBAL_ZERO, found.blocked)

    def test_rtprio_gate(self) -> None:
        mount = self.mount()
        self.slice(mount, "user.slice", 190000)
        found = defaults.inspect(
            self.policy(),
            "user.slice",
            self.limits(ZERO),
            privileged=False,
        )
        self.assertEqual(found.verdict, defaults.BLOCKED)
        self.assertIn(defaults.RTPRIO_ZERO, found.blocked)

    def test_root_ignores_rtprio(self) -> None:
        mount = self.mount()
        self.slice(mount, "user.slice", 190000)
        found = defaults.inspect(
            self.policy(),
            "user.slice",
            self.limits(ZERO),
            privileged=True,
        )
        self.assertEqual(found.verdict, defaults.BUDGETED)
        self.assertNotIn(defaults.RTPRIO_ZERO, found.blocked)

    def test_unreadable_global(self) -> None:
        self.mount()
        found_policy = self.policy()
        (self.base / "runtime").unlink()
        found = defaults.inspect(
            found_policy, limits=self.limits(UNLIMITED), privileged=True
        )
        self.assertEqual(found.verdict, defaults.UNKNOWN)
        self.assertIn(defaults.GLOBAL_READ, found.blocked)

    def test_no_cgroup_tree(self) -> None:
        found = defaults.inspect(
            self.policy(), limits=self.limits(UNLIMITED), privileged=True
        )
        self.assertEqual(found.verdict, defaults.UNKNOWN)
        self.assertIn(defaults.NO_CGROUP, found.blocked)

    def test_global_budget_is_budgeted(self) -> None:
        """0.0.4 内核不按组记账，全局预算已覆盖全部组，不算阻塞。"""
        self.global_mount()
        found = defaults.inspect(
            self.policy(), limits=self.limits(UNLIMITED), privileged=True
        )
        self.assertEqual(found.verdict, defaults.BUDGETED)
        self.assertEqual(found.blocked, ())
        self.assertTrue(found.usable)
        self.assertTrue(found.enabled)

    def test_v2_max_is_budgeted(self) -> None:
        self.v2()
        path = self.base / "user.slice"
        path.mkdir()
        (path / cgroup.V2_FILE).write_text("max 1000000\n", "ascii")
        found = defaults.inspect(
            self.policy(),
            "user.slice",
            self.limits(UNLIMITED),
            privileged=True,
        )
        self.assertEqual(found.kind, cgroup.Kind.V2)
        self.assertEqual(found.fresh, cgroup.MAX)
        self.assertEqual(found.verdict, defaults.BUDGETED)

    def test_rlimit_values(self) -> None:
        self.assertEqual(defaults.rlimit(self.limits(ZERO)), 0)
        self.assertIsNone(defaults.rlimit(self.limits(UNLIMITED)))
        self.assertIsNone(defaults.rlimit(str(self.base / "absent")))

    def test_dump_shape(self) -> None:
        self.mount()
        found = defaults.inspect(
            self.policy(), limits=self.limits(UNLIMITED), privileged=True
        )
        body = found.dump()
        for key in ("kind", "total", "period", "enabled", "root", "group",
                    "fresh", "rtprio", "privileged", "blocked", "verdict"):
            self.assertIn(key, body)
        self.assertEqual(body["blocked"], [defaults.NEW_GROUP_ZERO])


class SeedTest(Base):
    """补写口径与回读。"""

    def test_seeds_top_level(self) -> None:
        self.mount()
        steps = defaults.seed(self.policy(), ("user.slice",))
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].state, defaults.SEEDED)
        self.assertEqual(steps[0].want, 190000)
        self.assertEqual(cgroup.read(self.layout(), "user.slice"), 190000)

    def test_keeps_existing(self) -> None:
        mount = self.mount()
        self.slice(mount, "user.slice", 123000)
        steps = defaults.seed(self.policy(), ("user.slice",))
        self.assertEqual(steps[0].state, defaults.KEPT)
        self.assertEqual(steps[0].want, 123000)
        self.assertEqual(cgroup.read(self.layout(), "user.slice"), 123000)

    def test_nested_inherits_parent(self) -> None:
        mount = self.mount()
        self.slice(mount, "user.slice", 190000)
        child = mount / "user.slice" / "user-1000.slice"
        child.mkdir()
        (child / cgroup.V1_FILE).write_text("0\n", encoding="ascii")
        steps = defaults.seed(
            self.policy(), ("user.slice/user-1000.slice",)
        )
        self.assertEqual(steps[0].state, defaults.SEEDED)
        self.assertEqual(steps[0].want, 190000)
        self.assertEqual(
            cgroup.read(self.layout(), "user.slice/user-1000.slice"),
            190000,
        )

    def test_unlimited_global_kept(self) -> None:
        self.mount()
        steps = defaults.seed(self.policy(total="-1\n"), ("user.slice",))
        self.assertEqual(steps[0].state, defaults.KEPT)
        self.assertIn("不限", steps[0].note)
        self.assertIsNone(cgroup.read(self.layout(), "user.slice"))

    def test_global_zero_blocked(self) -> None:
        self.mount()
        steps = defaults.seed(self.policy(total="0\n"), ("user.slice",))
        self.assertEqual(steps[0].state, defaults.BLOCKED)
        self.assertIn("全局", steps[0].note)

    def test_no_layout_absent(self) -> None:
        steps = defaults.seed(self.policy(), ("user.slice",))
        self.assertEqual(steps[0].state, defaults.ABSENT)

    def test_global_budget_kept(self) -> None:
        """不按组记账的内核没有可写的份额，补写按「无需处理」放行。"""
        self.global_mount()
        steps = defaults.seed(self.policy(), ("user.slice",))
        self.assertEqual(steps[0].state, defaults.KEPT)
        self.assertIn("全局预算", steps[0].note)

    def test_subtree(self) -> None:
        mount = self.mount()
        self.slice(mount, "user.slice", 190000)
        for name in ("user-1000.slice", "user-1001.slice"):
            child = mount / "user.slice" / name
            child.mkdir()
            (child / cgroup.V1_FILE).write_text("0\n", encoding="ascii")
        steps = defaults.seed_subtree(self.policy(), "user.slice", 1)
        self.assertEqual(
            [item.group for item in steps],
            [
                "user.slice/user-1000.slice",
                "user.slice/user-1001.slice",
            ],
        )
        self.assertTrue(
            all(item.state == defaults.SEEDED for item in steps)
        )

    def test_subtree_without_tree(self) -> None:
        steps = defaults.seed_subtree(self.policy(), "user.slice", 1)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].state, defaults.ABSENT)

    def test_never_writes_unlimited(self) -> None:
        self.mount()
        defaults.seed(self.policy(), ("user.slice",))
        path = self.layout()
        text = (
            cgroup.target(path, "user.slice") / cgroup.V1_FILE
        ).read_text(encoding="ascii").strip()
        self.assertNotEqual(text, cgroup.UNLIMITED)


class DefaultsCliTest(Base):
    """命令行的退出码与 JSON 形状。"""

    def test_budgeted_exit_zero(self) -> None:
        mount = self.mount()
        self.slice(mount, "user.slice", 190000)
        argv = self.args("defaults") + [
            "--group", "user.slice",
            "--limits", self.limits(UNLIMITED),
        ]
        code, out = run(argv)
        self.assertEqual(code, 0)
        self.assertIn("verdict=budgeted", out)

    def test_new_group_exit_partial(self) -> None:
        self.mount()
        argv = self.args("defaults") + ["--limits", self.limits(UNLIMITED)]
        code, out = run(argv)
        self.assertEqual(code, cli.EXIT_PARTIAL)
        self.assertIn("new-group-zero", out)

    def test_unreadable_exit_one(self) -> None:
        (self.base / "runtime").unlink()
        argv = self.args("defaults") + ["--limits", self.limits(UNLIMITED)]
        code, _ = run(argv)
        self.assertEqual(code, cli.EXIT_FAIL)

    def test_json_shape(self) -> None:
        self.mount()
        argv = self.args("defaults") + [
            "--json",
            "--limits", self.limits(UNLIMITED),
        ]
        _, out = run(argv)
        body = json.loads(out)
        self.assertEqual(body["verdict"], defaults.BLOCKED)
        self.assertIn(defaults.NEW_GROUP_ZERO, body["blocked"])

    def test_seed_group(self) -> None:
        self.mount()
        argv = self.args("seed") + ["--group", "user.slice"]
        code, out = run(argv)
        self.assertEqual(code, 0)
        self.assertIn("seeded", out)
        self.assertEqual(cgroup.read(self.layout(), "user.slice"), 190000)

    def test_seed_requires_target(self) -> None:
        self.mount()
        self.assertEqual(run(self.args("seed"))[0], cli.EXIT_USAGE)

    def test_seed_json(self) -> None:
        self.mount()
        argv = self.args("seed") + ["--json", "--group", "user.slice"]
        _, out = run(argv)
        body = json.loads(out)
        self.assertEqual(body["steps"][0]["state"], defaults.SEEDED)


if __name__ == "__main__":
    unittest.main()
