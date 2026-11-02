# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 实时能力矩阵回归测试
#
# 用一棵临时机器覆盖三种等级与三种标志：ready 要求 PREEMPT_RT、带宽与
# 组记账三项齐全；soft 只要求带宽可用；runtime=0 与接口读不到必须分开，
# 前者是「不支持」，后者是「这次读不到」，混为一谈会让运维去换内核。
# 命令行层单独验证退出码与 --json 形状，门禁据此判读。

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airymax_rt import cli, cgroup, matrix  # noqa: E402
from rt_fixture import Machine  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.machine = Machine(Path(self._dir.name))

    def full(self) -> None:
        self.machine.realtime()
        self.machine.budget()
        self.machine.group()
        self.machine.deadline()
        self.machine.governor()
        self.machine.clocksource()
        self.machine.cmdline.write_text(
            "threadirqs isolcpus=2,3 nohz_full=2,3", encoding="ascii"
        )


class GradeTest(Base):
    """三项核心条件如何汇总成等级。"""

    def test_ready(self) -> None:
        self.full()
        found = matrix.collect(self.machine.roots())
        self.assertIs(found.grade, matrix.Grade.READY)
        self.assertEqual(found.pending, ())

    def test_soft_without_realtime(self) -> None:
        self.full()
        (self.machine.sys / "kernel" / "realtime").unlink()
        found = matrix.collect(self.machine.roots())
        self.assertIs(found.grade, matrix.Grade.SOFT)
        self.assertIn(matrix.REALTIME, found.pending)

    def test_none_when_budget_zero(self) -> None:
        self.full()
        self.machine.budget("0")
        found = matrix.collect(self.machine.roots())
        self.assertIs(found.grade, matrix.Grade.NONE)
        self.assertIs(found.flag(matrix.BUDGET), matrix.Flag.NO)

    def test_unreadable_budget_is_unknown(self) -> None:
        self.full()
        (self.machine.proc / "sys" / "kernel"
         / "sched_rt_runtime_us").unlink()
        found = matrix.collect(self.machine.roots())
        self.assertIs(found.flag(matrix.BUDGET), matrix.Flag.UNKNOWN)
        self.assertIs(found.grade, matrix.Grade.NONE)
        self.assertNotIn(matrix.BUDGET, found.pending)


class FlagTest(Base):
    """单项标志的证据口径。"""

    def test_group_missing(self) -> None:
        found = matrix.collect(self.machine.roots())
        self.assertIs(found.flag(matrix.GROUP), matrix.Flag.NO)

    def test_group_unaccounted(self) -> None:
        self.machine.put(
            self.machine.cgroup / cgroup.CONTROLLER, "cpu memory"
        )
        found = matrix.collect(self.machine.roots())
        self.assertIs(found.flag(matrix.GROUP), matrix.Flag.NO)
        self.assertEqual(found.caps[2].detail, "统一树未暴露 cpu 控制器")

    def test_cmdline_unreadable(self) -> None:
        self.machine.cmdline.unlink()
        found = matrix.collect(self.machine.roots())
        self.assertIs(found.flag(matrix.THREADIRQS), matrix.Flag.UNKNOWN)
        self.assertIs(found.flag(matrix.ISOLATED), matrix.Flag.UNKNOWN)

    def test_isolated_alone(self) -> None:
        self.machine.cmdline.write_text("nohz_full=1", encoding="ascii")
        found = matrix.collect(self.machine.roots())
        self.assertIs(found.flag(matrix.ISOLATED), matrix.Flag.YES)

    def test_governor_absent(self) -> None:
        found = matrix.collect(self.machine.roots())
        self.assertIs(found.flag(matrix.GOVERNOR), matrix.Flag.UNKNOWN)

    def test_clocksource_not_tsc(self) -> None:
        self.machine.clocksource("hpet")
        found = matrix.collect(self.machine.roots())
        self.assertIs(found.flag(matrix.CLOCKSOURCE), matrix.Flag.NO)


class CliTest(Base):
    """命令行退出码与输出形状。"""

    def call(self, argv: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()

    def args(self, cmd: str) -> list[str]:
        return [
            cmd,
            "--sysroot", str(self.machine.sys),
            "--procroot", str(self.machine.proc),
            "--cgroup", str(self.machine.cgroup),
            "--cmdline", str(self.machine.cmdline),
        ]

    def test_ready_exit_zero(self) -> None:
        self.full()
        code, out = self.call(self.args("matrix"))
        self.assertEqual(code, 0)
        self.assertIn("grade=ready", out)
        self.assertIn("advice", out)

    def test_soft_exit_partial(self) -> None:
        self.full()
        (self.machine.sys / "kernel" / "realtime").unlink()
        code, out = self.call(self.args("matrix"))
        self.assertEqual(code, cli.EXIT_PARTIAL)
        self.assertIn("grade=soft", out)

    def test_none_exit_fail(self) -> None:
        code, _ = self.call(self.args("matrix"))
        self.assertEqual(code, cli.EXIT_FAIL)

    def test_json_shape(self) -> None:
        self.full()
        code, out = self.call(["--json"] + self.args("matrix"))
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["grade"], "ready")
        self.assertEqual(payload["pending"], [])
        names = [item["name"] for item in payload["caps"]]
        self.assertIn(matrix.GOVERNOR, names)


if __name__ == "__main__":
    unittest.main()
