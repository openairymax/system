# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 磁盘操作面契约测试
#
# 覆盖三面：动作与拒绝理由的 token 稳定性、Step/Plan 的构造不变量、
# 护栏判定顺序。执行路径不在本文件，由执行层测试承担；外来数据盘
# 不被护栏拒绝，只进入二次确认，这里锁住该边界。

from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airydisk import model, ops  # noqa: E402


def disk(name: str = "sdb", **kw: object) -> model.Disk:
    node = {
        "name": name,
        "size": 1000,
        "bus": model.Bus.NVME,
        "table": model.TableKind.GPT,
    }
    node.update(kw)
    return model.Disk(**node)


def step(action: ops.Action = ops.Action.FORMAT) -> ops.Step:
    return ops.Step(action=action, target="/dev/sdb1",
                    argv=("mkfs.ext4", "/dev/sdb1"))


class TokenTest(unittest.TestCase):
    """token 即协议：改名会破坏与 --json 消费方的一致性。"""

    def test_action_tokens(self) -> None:
        self.assertEqual(
            [action.value for action in ops.Action],
            ["wipe", "partition", "format", "mount"],
        )

    def test_refusal_tokens(self) -> None:
        self.assertEqual(
            [item.value for item in ops.Refusal],
            ["read-only", "removable", "hosts-system"],
        )

    def test_no_alias(self) -> None:
        for cls in (ops.Action, ops.Refusal):
            self.assertEqual(len(cls), len(cls.__members__), cls.__name__)

    def test_refusal_text(self) -> None:
        self.assertEqual(set(ops.REFUSAL_TEXT), set(ops.Refusal))
        texts = list(ops.REFUSAL_TEXT.values())
        self.assertEqual(len(texts), len(set(texts)))
        for text in texts:
            self.assertTrue(text.strip())


class StepTest(unittest.TestCase):
    """步骤必须自带目标与命令行，否则执行层无法判断是否可安全运行。"""

    def test_undo_default(self) -> None:
        self.assertEqual(step().undo, ())

    def test_no_argv(self) -> None:
        with self.assertRaises(ValueError):
            ops.Step(action=ops.Action.WIPE, target="/dev/sdb", argv=())

    def test_no_target(self) -> None:
        with self.assertRaises(ValueError):
            ops.Step(action=ops.Action.WIPE, target="",
                     argv=("wipefs", "-a", "/dev/sdb"))

    def test_frozen(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            step().target = "/dev/sdc"  # type: ignore[misc]


class PlanTest(unittest.TestCase):
    """计划是纯数据：空计划合法，匿名计划不合法。"""

    def test_empty(self) -> None:
        plan = ops.Plan(disk="sdb")
        self.assertTrue(plan.empty)
        self.assertEqual(plan.steps, ())
        self.assertEqual(plan.schema, ops.SCHEMA_VERSION)

    def test_no_disk(self) -> None:
        with self.assertRaises(ValueError):
            ops.Plan(disk="")

    def test_steps(self) -> None:
        plan = ops.Plan(disk="sdb", steps=(step(),))
        self.assertFalse(plan.empty)
        self.assertEqual(plan.steps[0].argv[0], "mkfs.ext4")

    def test_frozen(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            ops.Plan(disk="sdb").disk = "sdc"  # type: ignore[misc]


class RefuseTest(unittest.TestCase):
    """护栏判定：只读、可移动与承载系统三种盘无条件拒绝。"""

    def test_pass(self) -> None:
        self.assertIsNone(ops.refuse(disk(), "nvme0n1"))

    def test_no_host(self) -> None:
        self.assertIsNone(ops.refuse(disk(), None))

    def test_read_only(self) -> None:
        self.assertIs(ops.refuse(disk(ro=True), None), ops.Refusal.READ_ONLY)

    def test_removable(self) -> None:
        self.assertIs(ops.refuse(disk(removable=True), None),
                      ops.Refusal.REMOVABLE)

    def test_hosts_system(self) -> None:
        self.assertIs(ops.refuse(disk(), "sdb"), ops.Refusal.HOSTS_SYSTEM)

    def test_read_only_first(self) -> None:
        self.assertIs(ops.refuse(disk(ro=True, removable=True), "sdb"),
                      ops.Refusal.READ_ONLY)

    def test_foreign_allowed(self) -> None:
        part = model.Partition(name="sdb1", size=100,
                               fs=model.FsKind.NTFS, mount="/mnt/win")
        item = model.Disk(name="sdb", size=1000, bus=model.Bus.SATA,
                          table=model.TableKind.GPT, parts=(part,))
        self.assertIsNone(ops.refuse(item, "nvme0n1"))


if __name__ == "__main__":
    unittest.main()
