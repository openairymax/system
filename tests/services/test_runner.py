# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 磁盘执行器回归测试
#
# 真实执行路径只用 /bin/true、/bin/false 与 echo 这类无副作用命令，
# 不碰块设备；回滚路径用假执行器注入失败，覆盖「失败后反向撤销」
# 「缺 undo 记为未完净」「回滚自身失败」三种分支。超时用例以 which
# 守卫，宿主缺工具时跳过。

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airydisk import ops, runner  # noqa: E402


def step(target: str, argv: tuple[str, ...],
         undo: tuple[str, ...] = ()) -> ops.Step:
    return ops.Step(action=ops.Action.WIPE, target=target, argv=argv,
                    undo=undo)


class Fake:
    """按 argv[0] 决定退出码的假执行器，并记录调用顺序。"""

    def __init__(self, *failing: str) -> None:
        self.failing = set(failing)
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, item: ops.Step, timeout: int) -> runner.Result:
        self.calls.append(item.argv)
        rc = 1 if item.argv[0] in self.failing else 0
        return runner.Result(step=item, rc=rc)


class RunTest(unittest.TestCase):
    """真实进程：退出码、缺命令、超时与「不经 shell」。"""

    def test_ok(self) -> None:
        self.assertTrue(runner.run(step("/dev/sdb", ("/bin/true",))).ok)

    def test_fail(self) -> None:
        result = runner.run(step("/dev/sdb", ("/bin/false",)))
        self.assertEqual(result.rc, 1)
        self.assertFalse(result.ok)

    def test_missing(self) -> None:
        result = runner.run(step("/dev/sdb", ("airy-no-such-command",)))
        self.assertEqual(result.rc, runner.RC_MISSING)

    @unittest.skipUnless(shutil.which("echo"), "echo 不可用")
    def test_no_shell(self) -> None:
        result = runner.run(step("/dev/sdb", ("echo", "$HOME")))
        self.assertEqual(result.out, "$HOME\n")

    @unittest.skipUnless(shutil.which("sleep"), "sleep 不可用")
    def test_timeout(self) -> None:
        result = runner.run(step("/dev/sdb", ("sleep", "5")), timeout=0.1)
        self.assertEqual(result.rc, runner.RC_TIMEOUT)


class ApplyTest(unittest.TestCase):
    """顺序执行与失败回滚：回滚只在失败之后发生，且按相反顺序。"""

    def plan(self, *steps: ops.Step) -> ops.Plan:
        return ops.Plan(disk="sdb", steps=steps)

    def test_empty(self) -> None:
        outcome = runner.apply(self.plan(), Fake())
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.done, ())

    def test_success(self) -> None:
        both = (step("/dev/sdb", ("one",), ("uno",)),
                step("/dev/sdb1", ("two",), ("dos",)))
        fake = Fake()
        outcome = runner.apply(self.plan(*both), fake)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.done, both)
        self.assertEqual(fake.calls, [("one",), ("two",)])

    def test_rollback(self) -> None:
        both = (step("/dev/sdb", ("one",), ("uno",)),
                step("/dev/sdb1", ("two",), ("dos",)))
        fake = Fake("two")
        outcome = runner.apply(self.plan(*both), fake)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.done, both[:1])
        self.assertEqual(outcome.undone, both[:1])
        self.assertFalse(outcome.unclean)
        self.assertEqual(fake.calls[-1], ("uno",))

    def test_rollback_order(self) -> None:
        steps = (step("/dev/sdb", ("one",), ("uno",)),
                 step("/dev/sdb1", ("two",), ("dos",)),
                 step("/dev/sdb2", ("three",), ("tres",)))
        fake = Fake("three")
        runner.apply(self.plan(*steps), fake)
        self.assertEqual(fake.calls[-2:], [("dos",), ("uno",)])

    def test_no_undo(self) -> None:
        steps = (step("/dev/sdb", ("one",)),
                 step("/dev/sdb1", ("two",)))
        outcome = runner.apply(self.plan(*steps), Fake("two"))
        self.assertEqual(outcome.undone, ())
        self.assertTrue(outcome.unclean)

    def test_undo_fails(self) -> None:
        steps = (step("/dev/sdb", ("one",), ("uno",)),
                 step("/dev/sdb1", ("two",)))
        outcome = runner.apply(self.plan(*steps), Fake("two", "uno"))
        self.assertEqual(outcome.undone, ())
        self.assertTrue(outcome.unclean)


class ResultTest(unittest.TestCase):
    """结果对象是快照，供 --json 与日志直接取用。"""

    def test_ok(self) -> None:
        item = step("/dev/sdb", ("true",))
        self.assertTrue(runner.Result(step=item, rc=0).ok)
        self.assertFalse(runner.Result(step=item, rc=1).ok)

    def test_defaults(self) -> None:
        item = step("/dev/sdb", ("true",))
        result = runner.Result(step=item, rc=0)
        self.assertEqual(result.out, "")
        self.assertEqual(result.err, "")


if __name__ == "__main__":
    unittest.main()
