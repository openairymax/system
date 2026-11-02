# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# RT 带宽命令行回归测试
#
# 验证三个子命令的输出形状与退出码：0 全部落地或不限预算、3 部分落地、
# 1 未通过、2 用法错误。--json 的字段与 Report.dump() 同形，门禁据此
# 直接判读而不解析人类可读文本。

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

from airymax_rt import cli, cgroup, policy  # noqa: E402
from rt_fixture import Tree  # noqa: E402


def run(argv: list[str]) -> tuple[int, str]:
    """执行一次命令行，返回退出码与标准输出。"""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


class StatusTest(Tree):
    """只读子命令的判定与退出码。"""

    def test_ok(self) -> None:
        self.all_slices()
        code, out = run(self.args("status"))
        self.assertEqual(code, 0)
        self.assertIn("state=ok", out)
        self.assertIn("advice", out)

    def test_partial(self) -> None:
        mount = self.mount()
        self.slice(mount, "system.slice", 190000)
        code, out = run(self.args("status"))
        self.assertEqual(code, cli.EXIT_PARTIAL)
        self.assertIn("state=partial", out)

    def test_failed(self) -> None:
        self.mount()
        code, out = run(self.args("status"))
        self.assertEqual(code, cli.EXIT_FAIL)
        self.assertIn("state=failed", out)

    def test_disabled(self) -> None:
        self.all_slices()
        self.policy(total="-1\n")
        code, out = run(self.args("status"))
        self.assertEqual(code, 0)
        self.assertIn("state=disabled", out)

    def test_no_budget(self) -> None:
        (self.base / "runtime").unlink()
        code, _ = run(self.args("status"))
        self.assertEqual(code, cli.EXIT_FAIL)

    def test_no_cgroup(self) -> None:
        code, _ = run(self.args("status"))
        self.assertEqual(code, cli.EXIT_FAIL)

    def test_unaccounted(self) -> None:
        (self.base / cgroup.CONTROLLER).write_text("cpu memory\n", "ascii")
        code, out = run(self.args("status"))
        self.assertEqual(code, cli.EXIT_FAIL)
        self.assertIn("state=unaccounted", out)

    def test_json(self) -> None:
        self.all_slices()
        code, out = run(["--json"] + self.args("status"))
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["state"], "ok")
        self.assertEqual(payload["applied"], 4)
        self.assertEqual(payload["kind"], "v1")


class ApplyTest(Tree):
    """落地子命令。"""

    def test_writes_and_reports(self) -> None:
        mount = self.mount()
        code, out = run(self.args("apply"))
        self.assertEqual(code, 0)
        self.assertIn("state=ok", out)
        for name in policy.DEFAULT_SLICES:
            text = (mount / name / cgroup.V1_FILE).read_text(encoding="ascii")
            self.assertEqual(text.strip(), "190000")

    def test_second_run_is_idempotent(self) -> None:
        self.mount()
        self.assertEqual(run(self.args("apply"))[0], 0)
        self.assertEqual(run(self.args("apply"))[0], 0)


class ConfigTest(Tree):
    """参考文本只打印，不落盘。"""

    def test_reference(self) -> None:
        before = sorted(self.base.iterdir())
        code, out = run(["config"])
        self.assertEqual(code, 0)
        self.assertIn("kernel.sched_rt_runtime_us = 950000", out)
        self.assertIn("kernel.sched_rt_period_us = 1000000", out)
        self.assertEqual(sorted(self.base.iterdir()), before)


class UsageTest(unittest.TestCase):
    """缺少子命令按用法错误退出，不冒充判定未通过。"""

    def test_missing_command(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                cli.main([])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
