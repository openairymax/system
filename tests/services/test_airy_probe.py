# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 探针清点层的回归测试
#
# 覆盖三个判定面：十个事件的发现与可读性、两条通道的权限面与写入试探、
# 三处静态计数的解析。全部在临时机器上跑，不需要 root，也不碰宿主机的
# 真实 tracefs。

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from airymax_probe import EVENTS, channels, events, stats  # noqa: E402
from airy_probe_fixture import Tree  # noqa: E402


class EventTest(Tree):
    """事件发现只认 tracefs 里的实际节点。"""

    def test_all(self) -> None:
        found = events.discover(self.tracefs)
        self.assertEqual(len(found), len(EVENTS))
        self.assertTrue(all(item.okay for item in found))
        self.assertEqual(found[0].id, 526)

    def test_missing(self) -> None:
        self.drop("airy_ipc_recv")
        found = {item.name: item for item in events.discover(self.tracefs)}
        self.assertEqual(found["airy_ipc_recv"].state, events.UNAVAILABLE)
        self.assertEqual(found["airy_ipc_recv"].reason, "missing")

    def test_malformed(self) -> None:
        node = self.tracefs / "events" / "airy" / "airy_sched_switch" / "id"
        node.write_text("n/a\n", encoding="ascii")
        found = {item.name: item for item in events.discover(self.tracefs)}
        self.assertEqual(found["airy_sched_switch"].reason, "malformed")
        self.assertFalse(found["airy_sched_switch"].okay)


class ChannelTest(Tree):
    """通道检查读权限位，写入试探只回答内核收不收。"""

    def test_readonly(self) -> None:
        self.policy.chmod(0o400)
        found = {item.name: item for item in channels.inspect(self.paths())}
        self.assertTrue(found["policy"].present)
        self.assertEqual(found["policy"].mode, "0400")
        self.assertFalse(found["policy"].writable)
        self.assertTrue(found["agentrt"].present)

    def test_missing(self) -> None:
        self.policy.unlink()
        found = {item.name: item for item in channels.inspect(self.paths())}
        self.assertFalse(found["policy"].present)
        self.assertEqual(found["policy"].reason, "missing")

    def test_attempt(self) -> None:
        (self.agentrt / "present").unlink()
        found = {item.channel: item for item in channels.attempt(self.paths())}
        self.assertEqual(found["agentrt"].error, "enoent")
        self.assertFalse(found["agentrt"].delivered)
        policy = found["policy"]
        self.assertEqual(policy.delivered, policy.error is None)


class StatsTest(Tree):
    """解析器只接受内核实际打印的列数。"""

    def test_snapshot(self) -> None:
        found = stats.snapshot(self.paths())
        self.assertTrue(found.facts)
        self.assertEqual(found.cap_used, 0)
        self.assertEqual(found.stc_total, 12)
        self.assertEqual(dict(found.stc_policies)["agent"], 5)
        self.assertEqual(found.tables[0].name, "uring_guard")
        self.assertEqual(found.tables[0].deny, 1)

    def test_bad_row(self) -> None:
        self.policy.write_text(
            "# name hook rows\nuring uring_cmd 2\n", encoding="ascii"
        )
        found = stats.snapshot(self.paths())
        self.assertEqual(found.tables, ())

    def test_missing(self) -> None:
        self.cap.unlink()
        self.stc.unlink()
        found = stats.snapshot(self.paths())
        self.assertIsNone(found.cap_used)
        self.assertIsNone(found.stc_total)
        self.assertFalse(found.facts)


if __name__ == "__main__":
    unittest.main()