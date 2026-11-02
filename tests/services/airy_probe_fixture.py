# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 探针测试共用的临时机器
#
# 十个事件、两条注入通道与三处静态计数散在四棵子树里，判定层与命令行层
# 需要同一棵「带 tracefs、securityfs、procfs 与 debugfs 的临时机器」。
# 把它收在一处，两个测试文件不再各抄一份目录拼装逻辑，测试树本身也不会
# 因为抄漏某个节点而让两条用例悄悄测不同的东西。

"""Shared temporary machine for the trace probe tests (X-02)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from airymax_probe import EVENTS, Paths

POLICY_HEAD = "# name hook rows evals allow deny audit complain fault"

POLICY_ROW = "uring_guard uring_cmd 2 8 6 1 1 0 0"

STC_BODY = "total 12\nrealtime 3\ninteractive 4\nagent 5\nbatch 0\n"


class Tree(unittest.TestCase):
    """一棵临时机器：默认十个事件齐全，通道与计数可读。"""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name)
        self.tracefs = self.base / "tracefs"
        self.policy = self.base / "policy"
        self.agentrt = self.base / "agentrt"
        self.cap = self.base / "cap_used"
        self.stc = self.base / "stats"
        self.machine()

    def machine(self) -> None:
        self.tracefs.mkdir()
        (self.tracefs / "tracing_on").write_text("0\n", encoding="ascii")
        (self.tracefs / "trace").write_text("", encoding="ascii")
        for index, name in enumerate(EVENTS, 526):
            node = self.tracefs / "events" / "airy" / name
            node.mkdir(parents=True, exist_ok=True)
            (node / "id").write_text(f"{index}\n", encoding="ascii")
            (node / "enable").write_text("0\n", encoding="ascii")
        self.policy.write_text(
            f"{POLICY_HEAD}\n{POLICY_ROW}\n", encoding="ascii"
        )
        self.agentrt.mkdir()
        (self.agentrt / "present").write_text("1\n", encoding="ascii")
        self.cap.write_text("0\n", encoding="ascii")
        self.stc.write_text(STC_BODY, encoding="ascii")

    def paths(self) -> Paths:
        return Paths(
            tracefs=str(self.tracefs),
            policy=str(self.policy),
            agentrt=str(self.agentrt),
            cap=str(self.cap),
            stc=str(self.stc),
        )

    def args(self, cmd: str) -> list[str]:
        """把子命令接到本机临时路径上。"""
        found = self.paths()
        return [
            cmd,
            "--tracefs",
            found.tracefs,
            "--policy",
            found.policy,
            "--agentrt",
            found.agentrt,
            "--cap",
            found.cap,
            "--stc",
            found.stc,
        ]

    def drop(self, name: str) -> None:
        """移除一个事件目录，模拟本机内核缺少该 tracepoint。"""
        node = self.tracefs / "events" / "airy" / name
        for item in node.iterdir():
            item.unlink()
        node.rmdir()

    def emit(self, name: str, times: int = 1) -> None:
        """把一个事件的若干条记录写进 trace 缓冲。"""
        body = "".join(
            f"  <...>-{index} [000] d... 1.0: {name}: field=0\n"
            for index in range(times)
        )
        with open(self.tracefs / "trace", "a", encoding="ascii") as handle:
            handle.write(body)

    def enable(self, name: str) -> str:
        node = self.tracefs / "events" / "airy" / name / "enable"
        return node.read_text(encoding="ascii").strip()