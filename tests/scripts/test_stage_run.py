# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 开机阶段运行器回归测试
#
# 以桩工具提供首行结论与退出码，桩 logger 承接契约库的日志调用，验证分档、
# 取值、一次开机只打一行、参数校验与退出码透传。桩只复刻对外接口（stdout
# 首行、退出码），不复制任何子命令的判据。

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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
printf 'zone cpu=%s\\n' "${FAKE_TAIL:-1}"
sleep "${FAKE_SLEEP:-0}"
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
        self.assertRegex(
            res.stdout,
            r"\[ 6/9 \] power           ok   ms=\d+ landed=49/49 cap=100%"
            r" severity=ok charge=97%\n",
        )

    def test_duration(self) -> None:
        """结论行带实测耗时：快阶段与慢阶段在日志里可以分开读。"""
        res = self.stage(FAKE_SLEEP="1")
        self.assertEqual(res.returncode, 0, res.stderr)
        got = re.search(r"ok   ms=(\d+)", res.stdout)
        self.assertIsNotNone(got, res.stdout)
        self.assertGreaterEqual(int(got.group(1)), 900)

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
        self.assertRegex(res.stdout, r"ok   ms=\d+ charge=97% landed=49/49\n")

    def test_no_facts(self) -> None:
        res = self.stage(keys="-")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertRegex(res.stdout, r"\[ 6/9 \] power           ok   ms=\d+\n")

    def test_raw(self) -> None:
        res = self.stage()
        self.assertIn("zone cpu=1", res.stdout)
        self.assertTrue(self.journal.exists())

    def test_repeat_quiet(self) -> None:
        """同一结论在 REPEAT 之内不再转发快照：285H 的 9886 行噪声根因。

        两轮的 ms= 必然不同，抑制仍命中，说明耗时没有进指纹。
        """
        self.assertEqual(self.stage().returncode, 0)
        res = self.stage()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("zone cpu", res.stdout)
        self.assertNotIn("[ 6/9 ]", res.stdout)

    def test_change_prints(self) -> None:
        """结论变化必须立即转发，抑制只针对逐字相同的重复。"""
        self.stage()
        res = self.stage(
            first=FIRST.replace("severity=ok", "severity=hot"), rc=3
        )
        self.assertEqual(res.returncode, 3, res.stderr)
        self.assertIn("zone cpu=1", res.stdout)
        self.assertNotIn("[ 6/9 ]", res.stdout)

    def test_detail_ignored(self) -> None:
        """逐轮都变的明细（温度、节流计数）不进指纹，否则抑制永不命中。"""
        self.stage()
        res = self.stage(FAKE_TAIL="99")
        self.assertNotIn("zone cpu", res.stdout)

    def test_interval_beat(self) -> None:
        """指纹不变但已过 REPEAT 时转发一次，证明仍在同一档位运行。"""
        self.stage()
        snap = self.state / "6.snap"
        age = time.time() - 10
        os.utime(snap, (age, age))
        res = self.stage(AIRY_STAGE_REPEAT="1")
        self.assertIn("zone cpu=1", res.stdout)

    def test_begin_precedes_result(self) -> None:
        """起始行先落，卡住的阶段才有一行可指认的「我在第几步」。"""
        res = self.stage()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(
            res.stdout.splitlines()[0], "[ 6/9 ] power           ..."
        )

    def test_heartbeat_while_slow(self) -> None:
        """跑过 BEAT 秒的阶段必须自报 used/limit，且结论行仍在最后。"""
        res = self.stage(
            AIRY_STAGE_BEAT="1", AIRY_STAGE_LIMIT="3", FAKE_SLEEP="2"
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("wait used=1s limit=3s", res.stdout)
        self.assertTrue(res.stdout.splitlines()[-1].endswith("charge=97%"))

    def test_no_heartbeat_when_fast(self) -> None:
        res = self.stage(AIRY_STAGE_BEAT="5")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("wait", res.stdout)

    def test_no_heartbeat_on_repeat(self) -> None:
        """timer 重跑已出过结论的阶段时不得再冒出没有结论的心跳行。"""
        self.stage()
        res = self.stage(AIRY_STAGE_BEAT="1", FAKE_SLEEP="2")
        self.assertNotIn("wait", res.stdout)
        self.assertNotIn("[ 6/9 ]", res.stdout)

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


class PrintContractCase(unittest.TestCase):
    """阶段单元的打印契约：心跳周期与预期上限必须与超时同源。

    这两个数字此前各自默认（30 / 60），谁也管不到单元的 TimeoutStartSec。
    结果是 30 秒超时的单元在 30 秒心跳前就被杀掉，屏幕上只剩一条没有下文
    的「...」，而打印出的上限还声称 60 秒。用真实的 units 目录做基底，
    逐个拿掉正确声明，确认校验器确实能拦住回退。
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.units = Path(self.tmp.name) / "units"
        shutil.copytree(ROOT / "units", self.units)
        # 阶段契约表按 units/../scripts/ui.sh 读取，夹具要还原真实布局。
        script = Path(self.tmp.name) / "scripts"
        script.mkdir()
        shutil.copy2(ROOT / "scripts" / "ui.sh", script / "ui.sh")

    def drop(self, name: str, line: str) -> None:
        path = self.units / name
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(line, ""), encoding="utf-8")

    def test_units_are_consistent(self) -> None:
        self.assertEqual(units_lint.check_dir(self.units), [])

    def test_beat_at_timeout_rejected(self) -> None:
        self.drop(
            "airy-boot-mount.service",
            "Environment=AIRY_STAGE_BEAT=10 AIRY_STAGE_LIMIT=30\n",
        )
        errs = units_lint.check_dir(self.units)
        self.assertTrue(
            any("no heartbeat before" in err for err in errs), errs
        )

    def test_limit_must_follow_timeout(self) -> None:
        self.drop(
            "airy-disk-mount.service",
            "Environment=AIRY_STAGE_LIMIT=120\n",
        )
        errs = units_lint.check_dir(self.units)
        self.assertTrue(
            any("AIRY_STAGE_LIMIT 60s" in err for err in errs), errs
        )


if __name__ == "__main__":
    unittest.main()
