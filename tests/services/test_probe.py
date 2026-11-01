# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 磁盘拓扑探测回归测试
#
# 样本按 lsblk -b -J 的真实输出形态构造，覆盖字段归一、分区角色与
# 拓扑分类三个判定面。样本中的 null、[SWAP]、空 fstype 与缺失字段
# 都取自目标机实测输出，用于锁住上游形态变化。真实执行 lsblk 的
# 用例由 which 守卫，宿主无该工具时跳过而不是失败。

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airydisk import model, probe  # noqa: E402


def disk(name: str, **kw: object) -> dict:
    node = {
        "name": name,
        "type": "disk",
        "size": 1000,
        "tran": "nvme",
        "pttype": "gpt",
        "ro": False,
        "rm": False,
        "model": "Sample",
        "serial": "S1",
    }
    node.update(kw)
    return node


def part(name: str, **kw: object) -> dict:
    node = {
        "name": name,
        "type": "part",
        "size": 100,
        "fstype": "ext4",
        "mountpoint": None,
        "partuuid": None,
        "label": None,
    }
    node.update(kw)
    return node


def topo(*devices: dict) -> model.Topology:
    return probe.parse(json.dumps({"blockdevices": list(devices)}))


class ShapeTest(unittest.TestCase):
    """输入形态：空、缺失、非磁盘节点与单位。"""

    def test_empty(self) -> None:
        self.assertIs(topo().kind, model.TopoKind.EMPTY)

    def test_blank_payload(self) -> None:
        self.assertIs(probe.parse("").kind, model.TopoKind.EMPTY)

    def test_no_key(self) -> None:
        self.assertIs(probe.parse("{}").kind, model.TopoKind.EMPTY)

    def test_size_bytes(self) -> None:
        topo_ = topo(disk("nvme0n1", size="2048000"))
        self.assertEqual(topo_.disks[0].size, 2048000)

    def test_size_absent(self) -> None:
        node = disk("sda")
        del node["size"]
        self.assertEqual(topo(node).disks[0].size, 0)

    def test_only_disks(self) -> None:
        topo_ = topo(disk("loop0", type="loop"), disk("sda"))
        self.assertEqual([item.name for item in topo_.disks], ["sda"])

    def test_only_parts(self) -> None:
        node = disk("sda")
        node["children"] = [
            part("sda1"),
            {"name": "crypt0", "type": "crypt"},
            part("sda2"),
        ]
        topo_ = topo(node)
        self.assertEqual(
            [item.name for item in topo_.disks[0].parts],
            ["sda1", "sda2"],
        )

    def test_parts_sorted_by_number(self) -> None:
        """parts 的契约是分区号升序，不能沿用 lsblk 的文本顺序。"""
        node = disk("nvme0n1")
        node["children"] = [
            part("nvme0n1p10"),
            part("nvme0n1p2"),
            part("nvme0n1p1"),
        ]
        self.assertEqual(
            [item.name for item in topo(node).disks[0].parts],
            ["nvme0n1p1", "nvme0n1p2", "nvme0n1p10"],
        )


class TokenTest(unittest.TestCase):
    """上游 token 到枚举的归一，未知值不得抛错。"""

    def test_bus(self) -> None:
        topo_ = topo(disk("sda", tran="sata"), disk("sdb", tran="usb"))
        self.assertEqual(
            [item.bus for item in topo_.disks],
            [model.Bus.SATA, model.Bus.USB],
        )

    def test_bus_unknown(self) -> None:
        topo_ = topo(disk("sda", tran=None), disk("sdb", tran="fc"))
        self.assertEqual(
            [item.bus for item in topo_.disks],
            [model.Bus.OTHER, model.Bus.OTHER],
        )

    def test_table(self) -> None:
        topo_ = topo(disk("sda", pttype="dos"), disk("sdb", pttype=None))
        self.assertEqual(
            [item.table for item in topo_.disks],
            [model.TableKind.MBR, model.TableKind.NONE],
        )

    def test_table_unknown(self) -> None:
        self.assertIs(
            topo(disk("sda", pttype="sun")).disks[0].table,
            model.TableKind.UNKNOWN,
        )

    def test_fs(self) -> None:
        node = disk("sda")
        node["children"] = [
            part("sda1", fstype="btrfs"),
            part("sda2", fstype="xfs"),
            part("sda3", fstype="zfs"),
            part("sda4", fstype=None),
        ]
        topo_ = topo(node)
        self.assertEqual(
            [item.fs for item in topo_.disks[0].parts],
            [model.FsKind.BTRFS, model.FsKind.XFS, model.FsKind.OTHER,
             model.FsKind.NONE],
        )

    def test_flags(self) -> None:
        topo_ = topo(disk("sda", ro=True, rm=True), disk("sdb", ro=False))
        self.assertEqual(
            [(item.ro, item.removable) for item in topo_.disks],
            [(True, True), (False, False)],
        )

    def test_null_text(self) -> None:
        node = disk("sda", model=None, serial=None)
        node["children"] = [part("sda1", label=None, partuuid=None)]
        topo_ = topo(node)
        self.assertEqual(topo_.disks[0].model, "")
        self.assertEqual(topo_.disks[0].serial, "")
        self.assertIsNone(topo_.disks[0].parts[0].label)
        self.assertIsNone(topo_.disks[0].parts[0].partuuid)


class RoleTest(unittest.TestCase):
    """分区角色决定操作面，判定顺序为挂载点优先于文件系统。"""

    def test_layout(self) -> None:
        node = disk("nvme0n1")
        node["children"] = [
            part("nvme0n1p1", fstype="vfat",
                 mountpoint=model.ESP_MOUNT),
            part("nvme0n1p2", fstype="btrfs", mountpoint="/"),
            part("nvme0n1p3", fstype="xfs", mountpoint=model.DATA_MOUNT),
            part("nvme0n1p4", fstype="swap", mountpoint="[SWAP]"),
        ]
        self.assertEqual(
            [item.role for item in topo(node).disks[0].parts],
            [model.Role.ESP, model.Role.SYSTEM, model.Role.DATA,
             model.Role.SWAP],
        )

    def test_system_mounts(self) -> None:
        node = disk("sda")
        node["children"] = [
            part("sda1", mountpoint="/var"),
            part("sda2", mountpoint="/usr"),
            part("sda3", mountpoint="/boot"),
        ]
        self.assertEqual(
            {item.role for item in topo(node).disks[0].parts},
            {model.Role.SYSTEM},
        )

    def test_swap_mount(self) -> None:
        node = disk("sda")
        node["children"] = [part("sda1", fstype="swap",
                                 mountpoint="[SWAP]")]
        self.assertIsNone(topo(node).disks[0].parts[0].mount)

    def test_foreign(self) -> None:
        node = disk("sda")
        node["children"] = [part("sda1", fstype="ntfs",
                                 mountpoint="/mnt/win")]
        self.assertIs(topo(node).disks[0].parts[0].role,
                      model.Role.FOREIGN)


class TopoTest(unittest.TestCase):
    """拓扑分类：裸盘、多盘与双系统共用同一条判定路径。"""

    def test_single(self) -> None:
        self.assertIs(topo(disk("sda")).kind, model.TopoKind.SINGLE)

    def test_multi(self) -> None:
        self.assertIs(topo(disk("sda"), disk("sdb")).kind,
                      model.TopoKind.MULTI)

    def test_dual_boot(self) -> None:
        node = disk("sda")
        node["children"] = [part("sda1", fstype="ntfs")]
        self.assertIs(topo(node).kind, model.TopoKind.DUAL_BOOT)

    def test_blank_not_foreign(self) -> None:
        node = disk("sda")
        node["children"] = [part("sda1", fstype=None)]
        self.assertIs(topo(node).kind, model.TopoKind.SINGLE)


class HostTest(unittest.TestCase):
    """挂载点反查承载磁盘：系统盘识别与安全护栏的共同前置。"""

    def layout(self) -> dict:
        node = disk("nvme0n1")
        node["children"] = [
            part("nvme0n1p1", fstype="vfat",
                 mountpoint=model.ESP_MOUNT),
            part("nvme0n1p2", fstype="btrfs", mountpoint="/"),
            part("nvme0n1p3", fstype="xfs", mountpoint=model.DATA_MOUNT),
        ]
        return node

    def test_root(self) -> None:
        topo_ = topo(self.layout())
        self.assertEqual(probe.host(topo_, "/").name, "nvme0n1")

    def test_esp_on_second(self) -> None:
        node = disk("sda")
        node["children"] = [part("sda1", fstype="btrfs", mountpoint="/")]
        esp = disk("sdb")
        esp["children"] = [part("sdb1", fstype="vfat",
                                mountpoint=model.ESP_MOUNT)]
        topo_ = topo(node, esp)
        self.assertEqual(probe.host(topo_, model.ESP_MOUNT).name, "sdb")
        self.assertEqual(probe.host(topo_, "/").name, "sda")

    def test_absent(self) -> None:
        topo_ = topo(self.layout())
        self.assertIsNone(probe.host(topo_, "/home"))

    def test_blank(self) -> None:
        self.assertIsNone(probe.host(topo(), "/"))


class ProbeTest(unittest.TestCase):
    """真实执行路径：宿主无 lsblk 时跳过，有则只要求形态合法。"""

    @unittest.skipUnless(shutil.which("lsblk"), "lsblk 不可用")
    def test_probe(self) -> None:
        topo_ = probe.probe()
        self.assertEqual(topo_.schema, model.SCHEMA_VERSION)
        self.assertIsInstance(topo_.disks, tuple)
        for item in topo_.disks:
            self.assertEqual(item.size, int(item.size))
            self.assertIsInstance(item.parts, tuple)


if __name__ == "__main__":
    unittest.main()
