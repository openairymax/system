# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 开机阶段运行器回归测试
#
# 以桩工具提供首行结论与退出码，桩 logger 承接契约库的日志调用，验证分档、
# 取值、一次开机只打一行、参数校验与退出码透传。桩只复刻对外接口（stdout
# 首行、退出码），不复制任何子命令的判据。

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

SCRIPT = ROOT / "scripts" / "stage_run.sh"

FIRST = (
    "airy-power: state=ready source=ac charge=97% duty=40% severity=ok"
    " rate=schedutil cap=100% turbo=on landed=49/49"
)
STAGE = "电源与热治理"

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

    def run_stage(self, *args: str, first: str = FIRST, rc: int = 0, **env2: str):
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}{os.pathsep}{env['PATH']}"
        env.update(
            {
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
            ["bash", str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def stage(self, notes: str = "3", keys: str = "landed,cap,severity,charge", **kw):
        return self.run_stage(STAGE, notes, keys, str(self.tool), **kw)

    def test_ok(self) -> None:
        res = self.stage()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn(
            "[ 6/9 ] 电源与热治理    ok   landed=49/49 cap=100%"
            " severity=ok charge=97%",
            res.stdout,
        )

    def test_note(self) -> None:
        res = self.stage(first=FIRST, rc=3)
        self.assertEqual(res.returncode, 3, res.stderr)
        self.assertIn("note", res.stdout)
        self.assertIn("severity=ok", res.stdout)

    def test_fail(self) -> None:
        res = self.stage(first=FIRST, rc=1)
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("FAIL", res.stdout)
        self.assertIn("rc=1", res.stdout)

    def test_once(self) -> None:
        self.assertEqual(self.stage().returncode, 0)
        res = self.stage()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("[ 6/9 ]", res.stdout)
        self.assertTrue((self.state / "6").exists())

    def test_order(self) -> None:
        res = self.stage(keys="charge,landed")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("ok   charge=97% landed=49/49\n", res.stdout)

    def test_no_facts(self) -> None:
        res = self.stage(keys="-")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("[ 6/9 ] 电源与热治理    ok\n", res.stdout)

    def test_raw(self) -> None:
        res = self.stage()
        self.assertIn("zone cpu=1", res.stdout)
        self.assertTrue(self.journal.exists())

    def test_usage(self) -> None:
        res = self.run_stage(STAGE, "3")
        self.assertEqual(res.returncode, 64, res.stderr)
        self.assertEqual(res.stdout, "")

    def test_unknown(self) -> None:
        res = self.run_stage("桌面美容", "3", "-", str(self.tool))
        self.assertEqual(res.returncode, 5, res.stderr)
        self.assertNotIn("[ 6/9 ]", res.stdout)
        self.assertIn("不在契约表", res.stderr)


class LintCase(unittest.TestCase):
    """脚本静态契约校验器。"""

    def test_lint_ok(self) -> None:
        self.assertEqual(units_lint.check_script(SCRIPT), [])


if __name__ == "__main__":
    unittest.main()
