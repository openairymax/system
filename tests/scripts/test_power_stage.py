# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 阶段 6「电源与热治理」入口回归测试
#
# 以桩 airy-power 提供首行结论与退出码，桩 logger 承接契约库的日志调用，
# 验证分档、取值、一次开机只打一行与退出码透传。桩只复刻对外接口
# （stdout 首行、退出码），不复制 airy-power 的任何判据。

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import units_lint  # noqa: E402

SCRIPT = ROOT / "scripts" / "power_stage.sh"

FIRST = (
    "airy-power: state=ready source=ac charge=97% duty=40% severity=ok"
    " rate=schedutil cap=100% turbo=on landed=49/49"
)

TOOL = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "${FAKE_FIRST:-}"
printf 'zone cpu=1\\n'
exit "${FAKE_RC:-0}"
"""

LOGGER = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "${@: -1}" >>"${FAKE_JOURNAL:?}"
"""


class StageCase(unittest.TestCase):
    """构造桩工具与隔离的看门目录。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.tool = base / "airy-power"
        self.tool.write_text(TOOL, encoding="utf-8")
        self.tool.chmod(0o755)
        self.journal = base / "journal.log"
        self.bin = base / "bin"
        self.bin.mkdir()
        logger = self.bin / "logger"
        logger.write_text(LOGGER, encoding="utf-8")
        logger.chmod(0o755)
        self.state = base / "ui"
        self.console = base / "console"

    def run_stage(self, first: str, rc: int = 0, **env2: str):
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}{os.pathsep}{env['PATH']}"
        env.update(
            {
                "AIRY_POWER_TOOL": str(self.tool),
                "FAKE_FIRST": first,
                "FAKE_RC": str(rc),
                "FAKE_JOURNAL": str(self.journal),
                "UI_STATE": str(self.state),
                "UI_CONSOLE": str(self.console),
                "LANG": "C.UTF-8",
            }
        )
        env.update(env2)
        return subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def test_ok(self) -> None:
        res = self.run_stage(FIRST)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("[ 6/9 ] 电源与热治理", res.stdout)
        self.assertIn(
            "[ 6/9 ] 电源与热治理    ok   landed=49/49 cap=100%"
            " severity=ok charge=97%",
            res.stdout,
        )

    def test_hot(self) -> None:
        res = self.run_stage(FIRST, rc=3)
        self.assertEqual(res.returncode, 3, res.stderr)
        self.assertIn("note", res.stdout)
        self.assertIn("severity=ok", res.stdout)

    def test_down(self) -> None:
        res = self.run_stage(FIRST, rc=1)
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("FAIL", res.stdout)
        self.assertIn("rc=1", res.stdout)

    def test_once(self) -> None:
        self.assertEqual(self.run_stage(FIRST).returncode, 0)
        res = self.run_stage(FIRST)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("[ 6/9 ]", res.stdout)
        self.assertTrue((self.state / "6").exists())

    def test_no_facts(self) -> None:
        res = self.run_stage("airy-power: state=ready")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("[ 6/9 ] 电源与热治理    ok\n", res.stdout)

    def test_raw(self) -> None:
        res = self.run_stage(FIRST)
        self.assertIn("zone cpu=1", res.stdout)
        self.assertTrue(self.journal.exists())


class LintCase(unittest.TestCase):
    """脚本静态契约校验器。"""

    def test_lint_ok(self) -> None:
        self.assertEqual(units_lint.check_script(SCRIPT), [])


if __name__ == "__main__":
    unittest.main()
