# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 背光命令行回归测试
#
# 验证四个子命令的输出形状与退出码：0 全部落地、3 部分落地或部分缺席、
# 1 有设备但全部失败、4 目标里一台设备都没有、2 用法错误。--json 与
# Report.dump() 同形，门禁据此直接判读而不解析人类可读文本。

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from airybright import cli  # noqa: E402
from bright_fixture import Tree  # noqa: E402


def run(argv: list[str]) -> tuple[int, str]:
    """执行一次命令行，返回退出码与标准输出。"""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


class StatusTest(Tree):
    """只读子命令的判定与退出码。"""

    def test_ok(self) -> None:
        code, out = run(self.args("status"))
        self.assertEqual(code, 0)
        self.assertIn("state=ok", out)
        self.assertIn("advice", out)
        self.assertIn("intel_backlight", out)

    def test_absent(self) -> None:
        self.wipe()
        code, out = run(self.args("status"))
        self.assertEqual(code, cli.EXIT_NA)
        self.assertIn("state=absent", out)

    def test_single(self) -> None:
        code, out = run(self.args("status", "keyboard"))
        self.assertEqual(code, 0)
        self.assertNotIn("intel_backlight", out)

    def test_json(self) -> None:
        code, out = run(self.args("status", "--json"))
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["mark"], "AIRYMAX-BRIGHT")
        self.assertEqual(data["state"], "ok")
        self.assertEqual(data["floor"], 1)
        self.assertEqual(len(data["devices"]), 2)


class SetTest(Tree):
    """写入子命令：落地、部分落地、全部失败三种收场。"""

    def test_screen(self) -> None:
        code, _ = run(self.args("set", "screen", "50"))
        self.assertEqual(code, 0)
        self.assertEqual(self.level(self.backlight, "intel_backlight"), 500)

    def test_open_floor(self) -> None:
        code, _ = run(self.args("set", "screen", "0", "--floor", "0"))
        self.assertEqual(code, 0)
        self.assertEqual(self.level(self.backlight, "intel_backlight"), 0)

    def test_partial(self) -> None:
        self.drop(self.leds, "platform::kbd_backlight")
        code, out = run(self.args("set", "all", "50"))
        self.assertEqual(code, cli.EXIT_PARTIAL)
        self.assertIn("state=partial", out)
        self.assertIn("absent", out)

    def test_failed(self) -> None:
        (self.backlight / "intel_backlight" / "brightness").unlink()
        code, out = run(self.args("set", "screen", "50"))
        self.assertEqual(code, cli.EXIT_FAIL)
        self.assertIn("error=gone", out)


class StepTest(Tree):
    """相对调节以每台设备自身读数为基准。"""

    def test_step(self) -> None:
        code, _ = run(self.args("step", "screen", "10"))
        self.assertEqual(code, 0)
        self.assertEqual(self.level(self.backlight, "intel_backlight"), 500)

    def test_down(self) -> None:
        code, _ = run(self.args("step", "screen", "-20"))
        self.assertEqual(code, 0)
        self.assertEqual(self.level(self.backlight, "intel_backlight"), 200)


class UsageTest(Tree):
    """用法错误必须在写任何节点之前判死。"""

    def test_pct(self) -> None:
        code, out = run(self.args("set", "screen", "101"))
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("usage-error", out)

    def test_floor(self) -> None:
        code, out = run(self.args("status", "--floor", "100"))
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("usage-error", out)

    def test_target(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            with contextlib.redirect_stderr(io.StringIO()):
                run(self.args("set", "panel", "50"))
        self.assertEqual(caught.exception.code, 2)

    def test_config(self) -> None:
        code, out = run(self.args("config"))
        self.assertEqual(code, 0)
        self.assertIn("AIRYMAX-BRIGHT config", out)
        self.assertIn("firmware>platform>raw", out)


class EntryTest(Tree):
    """入口脚本必须能独立运行：安装后只靠 site-packages 也不改路径。"""

    def test_entry(self) -> None:
        script = ROOT / "bin" / "airy-bright"
        argv = [sys.executable, str(script), *self.args("status", "--json")]
        done = subprocess.run(argv, capture_output=True, text=True,
                              check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["state"], "ok")


if __name__ == "__main__":
    unittest.main()
