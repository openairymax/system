# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 探针命令行的回归测试
#
# 三个子命令共用一份判定实现，因此用例只回答两件事：退出码能否被门禁
# 直接判读，输出里的证据是否与机器状态一致。窗口内的 sleep 被替换成
# 「往缓冲里写事件」，用例不依赖真实时钟，也不依赖本机内核。

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from airymax_probe import cli, report  # noqa: E402
from airy_probe_fixture import Tree  # noqa: E402

SLEEP = "airymax_probe.trace.time.sleep"


def run(argv: list[str]) -> tuple[int, str]:
    """执行一次命令行，返回退出码与标准输出。"""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


class StatusTest(Tree):
    """只读清点的判定与退出码。"""

    def test_ok(self) -> None:
        code, out = run(self.args("status"))
        self.assertEqual(code, 0)
        self.assertIn("state=ok", out)
        self.assertIn("readable=10/10", out)
        self.assertIn(report.OK_HINT, out)

    def test_partial(self) -> None:
        self.drop("airy_ipc_recv")
        code, out = run(self.args("status"))
        self.assertEqual(code, cli.EXIT_PARTIAL)
        self.assertIn("state=partial", out)
        self.assertIn("airy_ipc_recv", out)
        self.assertIn(report.SURFACE_HINT, out)

    def test_denied_hint(self) -> None:
        # 非特权用户跑探针时，处置建议必须是「加 sudo」，而不是「换内核」。
        with mock.patch.object(Path, "is_dir",
                               side_effect=PermissionError(13, "denied")):
            code, out = run(self.args("status"))
        self.assertEqual(code, cli.EXIT_FAIL)
        self.assertIn(report.DENIED_HINT, out)

    def test_blocked(self) -> None:
        argv = self.args("status")
        argv[2] = str(self.base / "absent")
        code, out = run(argv)
        self.assertEqual(code, cli.EXIT_FAIL)
        self.assertIn("state=blocked", out)
        self.assertIn(report.BLOCKED_HINT, out)

    def test_json(self) -> None:
        code, out = run(self.args("status") + ["--json"])
        self.assertEqual(code, 0)
        found = json.loads(out)
        self.assertEqual(found["probe"], "airy-probe")
        self.assertEqual(found["mode"], "status")
        self.assertEqual((found["readable"], found["total"]), (10, 10))
        self.assertEqual(found["events"][0]["id"], 526)
        self.assertEqual(found["stats"]["cap_used"], 0)
        self.assertEqual(found["channels"][0]["name"], "policy")


class SampleTest(Tree):
    """单轮采样的计数与失败留证。"""

    def test_counts(self) -> None:
        def fake(_: float) -> None:
            self.emit("airy_sched_switch", 2)

        with mock.patch(SLEEP, side_effect=fake):
            code, out = run(self.args("sample") + ["--window", "0.01"])
        self.assertEqual(code, 0)
        self.assertIn("hit=2", out)

    def test_idle(self) -> None:
        with mock.patch(SLEEP):
            code, out = run(self.args("sample") + ["--window", "0.01"])
        self.assertEqual(code, 0)
        self.assertIn("hit=0", out)
        self.assertIn("idle", out)

    def test_window(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            cli.main(["sample", "--window", "0"])
        self.assertEqual(caught.exception.code, 2)


class VerifyTest(Tree):
    """多轮采样回答计数是否随动作递增。"""

    def test_witnessed(self) -> None:
        state = {"round": 0}

        def fake(_: float) -> None:
            state["round"] += 1
            self.emit("airy_policy_decision", state["round"])

        with mock.patch(SLEEP, side_effect=fake):
            code, out = run(
                self.args("verify") + ["--window", "0.01", "--repeat", "3"]
            )
        self.assertEqual(code, 0)
        self.assertIn("witnessed=True", out)

    def test_idle(self) -> None:
        with mock.patch(SLEEP):
            code, out = run(
                self.args("verify") + ["--window", "0.01", "--repeat", "2"]
            )
        self.assertEqual(code, cli.EXIT_PARTIAL)
        self.assertIn("witnessed=False", out)
        self.assertIn(report.IDLE_HINT, out)

    def test_attempt(self) -> None:
        with mock.patch(SLEEP):
            code, out = run(
                self.args("verify")
                + ["--window", "0.01", "--repeat", "2", "--json"]
            )
        found = json.loads(out)
        self.assertEqual(code, cli.EXIT_PARTIAL)
        self.assertEqual([item["channel"] for item in found["attempts"]],
                         ["policy", "agentrt"])
        self.assertEqual(len(found["samples"]), 2)
        self.assertEqual(found["hint"], report.IDLE_HINT)

    def test_repeat(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            cli.main(["verify", "--repeat", "1"])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()