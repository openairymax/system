# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 电源与温度命令行回归测试
#
# 验证三个子命令的输出形状与退出码：0 档位落地且温度未到 hot、3 部分
# 落地或温度已达 hot、1 全部写入失败、2 用法错误。--json 的字段与
# Report.dump() 同形，门禁据此直接判读而不解析人类可读文本。

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

from airymax_power import cli  # noqa: E402
from power_fixture import Tree  # noqa: E402


def run(argv: list[str]) -> tuple[int, str]:
    """执行一次命令行，返回退出码与标准输出。"""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


class StatusTest(Tree):
    """只读子命令的判定与退出码。"""

    def test_drift(self) -> None:
        code, out = run(self.args("status"))
        self.assertEqual(code, cli.EXIT_FAIL)
        self.assertIn("state=failed", out)
        self.assertIn("advice", out)

    def test_ok(self) -> None:
        run(self.args("apply"))
        code, out = run(self.args("status"))
        self.assertEqual(code, 0)
        self.assertIn("state=ok", out)

    def test_partial(self) -> None:
        run(self.args("apply"))
        (self.base / "cpu/policy0/scaling_governor").write_text(
            "performance\n", encoding="ascii")
        code, out = run(self.args("status"))
        self.assertEqual(code, cli.EXIT_PARTIAL)
        self.assertIn("state=partial", out)

    def test_hot(self) -> None:
        self.zone("SEN1", 95000, critical=100000, index=1)
        code, out = run(self.args("apply"))
        self.assertEqual(code, cli.EXIT_PARTIAL)
        self.assertIn("severity=hot", out)

    def test_unsupported(self) -> None:
        missing = self.base / "missing"
        argv = self.args("status")
        argv[2] = str(missing)
        argv[6] = str(missing)
        code, out = run(argv)
        self.assertEqual(code, 0)
        self.assertIn("state=unsupported", out)

    def test_json(self) -> None:
        code, out = run(self.args("status") + ["--json"])
        self.assertEqual(code, cli.EXIT_FAIL)
        found = json.loads(out)
        self.assertEqual(found["state"], "failed")
        self.assertEqual(found["source"], "ac")
        self.assertIsNone(found["charge"])
        self.assertEqual(found["duty"], "none")
        self.assertEqual(found["present"], 3)
        self.assertEqual(found["zones"][0]["name"], "x86_pkg_temp")


class ApplyTest(Tree):
    """落地子命令写 sysfs 并回读。"""

    def test_apply(self) -> None:
        code, _ = run(self.args("apply"))
        self.assertEqual(code, 0)
        self.assertIn("balanced", self.profile.read_text(encoding="ascii"))
        self.assertEqual(
            (self.base / "cpu/policy0/scaling_governor").read_text("ascii"),
            "powersave\n",
        )

    def test_battery(self) -> None:
        self.battery()
        code, out = run(self.args("apply"))
        self.assertEqual(code, 0)
        self.assertIn("source=battery", out)
        self.assertIn("charge=80%", out)
        self.assertIn("low-power", self.profile.read_text(encoding="ascii"))

    def test_low_charge(self) -> None:
        """临界电量在 JSON 里报 poweroff，并真的请求了受控关机。"""
        self.battery(capacity=5)
        run(self.args("apply"))
        code, out = run(self.args("status") + ["--json"])
        self.assertEqual(code, 0)
        found = json.loads(out)
        self.assertEqual((found["source"], found["charge"]), ("battery", 5))
        self.assertEqual(found["duty"], "poweroff")
        self.assertEqual(self.calls("poweroff"), ["poweroff --no-block"])
        self.assertEqual(self.calls("wall"), [])


class ConfigTest(unittest.TestCase):
    """参考文本不落盘。"""

    def test_reference(self) -> None:
        code, out = run(["config"])
        self.assertEqual(code, 0)
        self.assertIn("balanced", out)
        self.assertIn("schedutil", out)
        self.assertIn("critical", out)
        self.assertIn("10%", out)
        self.assertIn("5%", out)

    def test_usage(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            cli.main(["bogus"])
        self.assertEqual(caught.exception.code, 2)
