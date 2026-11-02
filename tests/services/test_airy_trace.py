# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 采样窗口的回归测试
#
# ftrace 的开关是全局机器状态，因此每条用例都回答两个问题：窗口内算出来
# 的计数对不对，窗口结束后机器是否回到采样前的样子。sleep 被替换成「往
# 缓冲里写事件」，用例不依赖真实时钟，也不依赖本机内核。

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from airymax_probe import EVENTS, trace  # noqa: E402
from airy_probe_fixture import Tree  # noqa: E402

SLEEP = "airymax_probe.trace.time.sleep"


class RecordTest(Tree):
    """开窗、计数、回写三步各自可验。"""

    def test_counts(self) -> None:
        def fake(_: float) -> None:
            self.assertEqual(self.enable("airy_ipc_send"), "1")
            self.emit("airy_sched_switch", 2)
            self.emit("airy_policy_decision")

        with mock.patch(SLEEP, side_effect=fake):
            shot = trace.record(self.tracefs, 0.01, EVENTS)
        self.assertEqual(shot.state, trace.OK)
        self.assertEqual(shot.count("airy_sched_switch"), 2)
        self.assertEqual(shot.count("airy_policy_decision"), 1)
        self.assertEqual(shot.hit, 3)

    def test_restore(self) -> None:
        node = self.tracefs / "events" / "airy" / "airy_ipc_send" / "enable"
        node.write_text("1\n", encoding="ascii")
        with mock.patch(SLEEP):
            trace.record(self.tracefs, 0.01, EVENTS)
        self.assertEqual(self.enable("airy_ipc_send"), "1")
        self.assertEqual(self.enable("airy_sched_switch"), "0")
        on = self.tracefs / "tracing_on"
        self.assertEqual(on.read_text(encoding="ascii").strip(), "0")

    def test_keep_on(self) -> None:
        on = self.tracefs / "tracing_on"
        on.write_text("1\n", encoding="ascii")
        with mock.patch(SLEEP):
            trace.record(self.tracefs, 0.01, EVENTS)
        self.assertEqual(on.read_text(encoding="ascii").strip(), "1")

    def test_idle(self) -> None:
        with mock.patch(SLEEP):
            shot = trace.record(self.tracefs, 0.01, EVENTS)
        self.assertEqual(shot.state, trace.IDLE)
        self.assertEqual(shot.hit, 0)

    def test_missing(self) -> None:
        shot = trace.record(self.base / "absent", 0.01, EVENTS)
        self.assertEqual(shot.state, trace.UNAVAILABLE)
        self.assertEqual(shot.reason, "missing")

    def test_arm_fail(self) -> None:
        self.drop("airy_lockdown_reject")
        shot = trace.record(self.tracefs, 0.01, EVENTS)
        self.assertEqual(shot.state, trace.UNAVAILABLE)
        self.assertEqual(shot.reason, "enoent")
        on = self.tracefs / "tracing_on"
        self.assertEqual(on.read_text(encoding="ascii").strip(), "0")


if __name__ == "__main__":
    unittest.main()