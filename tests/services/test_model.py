# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 磁盘设备模型契约测试
#
# 校验 model 的类型契约：枚举 token 与 sysfs/lsblk 字段逐字一致、
# 枚举无别名、数据类不可变、默认值合法可组合。探测行为不在本文件
# 覆盖，由探测层的测试承担。

from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airydisk import model  # noqa: E402


class TokenTest(unittest.TestCase):
    """枚举取值即协议：改名会破坏与 lsblk/sysfs 的对照关系。"""

    def test_bus_tokens(self) -> None:
        self.assertEqual(
            [bus.value for bus in model.Bus],
            ["nvme", "sata", "scsi", "usb", "mmc", "virtio", "other"],
        )

    def test_table_tokens(self) -> None:
        self.assertEqual(
            {kind.value for kind in model.TableKind},
            {"gpt", "dos", "", "unknown"},
        )

    def test_fs_tokens(self) -> None:
        self.assertEqual(
            {kind.value for kind in model.FsKind},
            {"ext4", "xfs", "btrfs", "vfat", "ntfs", "swap", "", "other"},
        )

    def test_topo_tokens(self) -> None:
        self.assertEqual(
            [kind.value for kind in model.TopoKind],
            ["empty", "single", "multi", "dual-boot"],
        )

    def test_role_tokens(self) -> None:
        self.assertEqual(
            {role.value for role in model.Role},
            {"system", "data", "esp", "swap", "foreign"},
        )

    def test_no_alias(self) -> None:
        for cls in (model.Bus, model.TableKind, model.FsKind, model.Role,
                    model.TopoKind):
            self.assertEqual(len(cls), len(cls.__members__), cls.__name__)


class DefaultTest(unittest.TestCase):
    """默认值必须能在不补任何字段的情况下组合出合法对象。"""

    def test_partition_defaults(self) -> None:
        part = model.Partition(name="sda1", size=512, fs=model.FsKind.EXT4)
        self.assertIs(part.role, model.Role.FOREIGN)
        self.assertIsNone(part.mount)
        self.assertIsNone(part.label)
        self.assertIsNone(part.partuuid)

    def test_disk_defaults(self) -> None:
        disk = model.Disk(
            name="sda", size=1024, bus=model.Bus.SATA,
            table=model.TableKind.GPT)
        self.assertEqual(disk.parts, ())
        self.assertFalse(disk.ro)
        self.assertFalse(disk.removable)
        self.assertEqual(disk.model, "")
        self.assertEqual(disk.serial, "")

    def test_topology_schema(self) -> None:
        topo = model.Topology(kind=model.TopoKind.EMPTY)
        self.assertEqual(topo.disks, ())
        self.assertEqual(topo.schema, model.SCHEMA_VERSION)
        self.assertEqual(model.SCHEMA_VERSION, 1)


class ImmutableTest(unittest.TestCase):
    """探测结果是快照：任何层都不得就地改写，只能生成新对象。"""

    def test_partition_frozen(self) -> None:
        part = model.Partition(name="sda1", size=512, fs=model.FsKind.EXT4)
        with self.assertRaises(FrozenInstanceError):
            part.size = 1024  # type: ignore[misc]

    def test_disk_frozen(self) -> None:
        disk = model.Disk(
            name="sda", size=1024, bus=model.Bus.NVME,
            table=model.TableKind.GPT)
        with self.assertRaises(FrozenInstanceError):
            disk.ro = True  # type: ignore[misc]

    def test_topology_frozen(self) -> None:
        topo = model.Topology(kind=model.TopoKind.SINGLE)
        with self.assertRaises(FrozenInstanceError):
            topo.kind = model.TopoKind.MULTI  # type: ignore[misc]


class ComposeTest(unittest.TestCase):
    """磁盘、分区与拓扑必须能无损嵌套，分区顺序由调用方保证。"""

    def test_nested(self) -> None:
        parts = (
            model.Partition(name="nvme0n1p1", size=200, fs=model.FsKind.VFAT,
                            mount="/boot/efi", role=model.Role.ESP),
            model.Partition(name="nvme0n1p2", size=800, fs=model.FsKind.BTRFS,
                            mount="/", role=model.Role.SYSTEM),
        )
        disk = model.Disk(name="nvme0n1", size=1000, bus=model.Bus.NVME,
                          table=model.TableKind.GPT, parts=parts)
        topo = model.Topology(kind=model.TopoKind.SINGLE, disks=(disk,))
        self.assertIs(topo.disks[0].parts[1], parts[1])
        self.assertEqual(
            [part.role for part in topo.disks[0].parts],
            [model.Role.ESP, model.Role.SYSTEM],
        )

    def test_system_mounts(self) -> None:
        self.assertIsInstance(model.SYSTEM_MOUNTS, tuple)
        self.assertIn("/", model.SYSTEM_MOUNTS)
        self.assertIn(model.ESP_MOUNT, model.SYSTEM_MOUNTS)
        self.assertNotIn(model.DATA_MOUNT, model.SYSTEM_MOUNTS)
        for mount in model.SYSTEM_MOUNTS:
            self.assertTrue(mount.startswith("/"))


if __name__ == "__main__":
    unittest.main()
