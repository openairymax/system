# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 数据分区扩容判定回归测试
#
# 判定只吃探测快照，不触设备：构造末位 ext4/xfs/btrfs、缺数据分区、
# 数据分区不在盘尾、未知文件系统五种现场，核对状态 token、承载盘名与
# 计划步骤；再核对缺失工具与普通失败的修复建议都指向具体动作。

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airydisk import grow, model, ops  # noqa: E402


def part(name: str, mount: str | None = None,
         fs: model.FsKind = model.FsKind.EXT4,
         role: model.Role = model.Role.FOREIGN) -> model.Partition:
    return model.Partition(name=name, size=1024, fs=fs, mount=mount,
                           role=role)


def disk(name: str, *parts: model.Partition,
         bus: model.Bus = model.Bus.NVME) -> model.Disk:
    return model.Disk(name=name, size=1024**3, bus=bus,
                      table=model.TableKind.GPT, parts=parts)


def topo(*disks: model.Disk) -> model.Topology:
    return model.Topology(kind=model.TopoKind.MULTI, disks=disks)


def single(fs: model.FsKind = model.FsKind.EXT4) -> model.Topology:
    return topo(disk(
        "nvme0n1",
        part("nvme0n1p1", "/", model.FsKind.EXT4, model.Role.SYSTEM),
        part("nvme0n1p2", model.DATA_MOUNT, fs, model.Role.DATA),
    ))


def dual(fs: model.FsKind = model.FsKind.EXT4) -> model.Topology:
    return topo(
        disk("nvme0n1",
             part("nvme0n1p1", "/", model.FsKind.EXT4, model.Role.SYSTEM)),
        disk("sda", part("sda1", model.DATA_MOUNT, fs, model.Role.DATA),
             bus=model.Bus.SATA),
    )


class PlanTest(unittest.TestCase):
    """扩容计划：末位分区两步骤，数据分区与系统分区互不牵连。"""

    def test_ext4(self) -> None:
        found = grow.plan(single())
        self.assertEqual(found.state, grow.State.OK)
        self.assertEqual(found.disk, "nvme0n1")
        self.assertEqual(found.part, "nvme0n1p2")
        steps = found.plan.steps
        self.assertEqual(steps[0].argv,
                         (grow.GROWPART, "/dev/nvme0n1", "2"))
        self.assertEqual(steps[1].argv, (grow.RESIZE2FS, "/dev/nvme0n1p2"))
        self.assertEqual(steps[1].action, ops.Action.RESIZE)

    def test_dual(self) -> None:
        """双盘时只扩数据盘，系统盘不进入计划。"""
        found = grow.plan(dual())
        self.assertTrue(found.ok)
        self.assertEqual(found.disk, "sda")
        self.assertTrue(all(s.target.startswith("/dev/sda")
                            for s in found.plan.steps))

    def test_xfs(self) -> None:
        """xfs 在线扩容收挂载点，target 仍是承载改动的分区。"""
        found = grow.plan(single(model.FsKind.XFS))
        self.assertTrue(found.ok)
        step = found.plan.steps[1]
        self.assertEqual(step.argv,
                         (grow.XFS_GROWFS, "-d", model.DATA_MOUNT))
        self.assertEqual(step.target, "/dev/nvme0n1p2")

    def test_btrfs(self) -> None:
        found = grow.plan(single(model.FsKind.BTRFS))
        self.assertTrue(found.ok)
        self.assertEqual(
            found.plan.steps[1].argv,
            (grow.BTRFS, "filesystem", "resize", "max", model.DATA_MOUNT),
        )

    def test_no_data(self) -> None:
        found = grow.plan(topo(disk(
            "nvme0n1",
            part("nvme0n1p1", "/", model.FsKind.EXT4, model.Role.SYSTEM),
        )))
        self.assertEqual(found.state, grow.State.NO_DATA)
        self.assertIsNone(found.plan)
        self.assertIn("/data", found.advice)

    def test_not_last(self) -> None:
        """盘尾还有分区时判 not-last，不给计划也不建议「顺手补一下」。"""
        found = grow.plan(topo(disk(
            "nvme0n1",
            part("nvme0n1p1", model.DATA_MOUNT, model.FsKind.EXT4,
                 model.Role.DATA),
            part("nvme0n1p2", "/", model.FsKind.EXT4, model.Role.SYSTEM),
        )))
        self.assertEqual(found.state, grow.State.NOT_LAST)
        self.assertIsNone(found.plan)
        self.assertIn("重装", found.advice)

    def test_no_fs(self) -> None:
        found = grow.plan(single(model.FsKind.NTFS))
        self.assertEqual(found.state, grow.State.NO_FS)
        self.assertIsNone(found.plan)
        self.assertIn("ntfs", found.advice)


class RepairTest(unittest.TestCase):
    """失败建议必须可执行：缺工具给包名，其余给取证入口。"""

    def test_missing_tool(self) -> None:
        step = ops.Step(action=ops.Action.GROW, target="/dev/sda",
                        argv=(grow.GROWPART, "/dev/sda", "1"))
        self.assertIn("cloud-utils-growpart", grow.repair(step, 127))

    def test_missing_resize(self) -> None:
        step = ops.Step(action=ops.Action.RESIZE, target="/dev/sda1",
                        argv=(grow.XFS_GROWFS, "-d", model.DATA_MOUNT))
        self.assertIn("xfsprogs", grow.repair(step, 127))

    def test_other_failure(self) -> None:
        step = ops.Step(action=ops.Action.RESIZE, target="/dev/sda1",
                        argv=(grow.RESIZE2FS, "/dev/sda1"))
        self.assertIn("dmesg", grow.repair(step, 1))


if __name__ == "__main__":
    unittest.main()
