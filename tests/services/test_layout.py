# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 布局判定回归测试
#
# 判定只吃探测快照，不触设备：构造单盘切分、双盘、缺数据分区、缺系统
# 分区、空拓扑与数据分区不在盘尾六种现场，核对 kind/verdict 与承载盘
# 名。断言用 token，不比对自由文本。

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airydisk import layout, model  # noqa: E402


def part(name: str, mount: str | None = None,
         role: model.Role = model.Role.FOREIGN) -> model.Partition:
    return model.Partition(name=name, size=1024, fs=model.FsKind.EXT4,
                           mount=mount, role=role)


def disk(name: str, *parts: model.Partition,
         bus: model.Bus = model.Bus.NVME) -> model.Disk:
    return model.Disk(name=name, size=1024**3, bus=bus,
                      table=model.TableKind.GPT, parts=parts)


def topo(*disks: model.Disk) -> model.Topology:
    return model.Topology(kind=model.TopoKind.MULTI, disks=disks)


def single() -> model.Topology:
    """单盘切分：数据分区是盘上最后一号分区。"""
    return topo(disk(
        "nvme0n1",
        part("nvme0n1p1", model.ESP_MOUNT, model.Role.ESP),
        part("nvme0n1p2", "/boot", model.Role.SYSTEM),
        part("nvme0n1p3", None, model.Role.SWAP),
        part("nvme0n1p4", "/", model.Role.SYSTEM),
        part("nvme0n1p5", model.DATA_MOUNT, model.Role.DATA),
    ))


def dual() -> model.Topology:
    """双盘：系统盘只放系统分区，数据分区独占第二块盘。"""
    return topo(
        disk("nvme0n1",
             part("nvme0n1p1", model.ESP_MOUNT, model.Role.ESP),
             part("nvme0n1p2", "/", model.Role.SYSTEM)),
        disk("sda", part("sda1", model.DATA_MOUNT, model.Role.DATA),
             bus=model.Bus.SATA),
    )


class InspectTest(unittest.TestCase):
    """布局判定：系统与数据两侧独立挂载，数据分区在盘尾才可扩容。"""

    def test_single(self) -> None:
        found = layout.inspect(single())
        self.assertEqual(found.kind, layout.SINGLE)
        self.assertEqual(found.state, layout.State.OK)
        self.assertEqual(found.root, "nvme0n1")
        self.assertEqual(found.data, "nvme0n1")

    def test_dual(self) -> None:
        found = layout.inspect(dual())
        self.assertEqual(found.kind, layout.DUAL)
        self.assertTrue(found.ok)
        self.assertEqual(found.root, "nvme0n1")
        self.assertEqual(found.data, "sda")

    def test_no_data(self) -> None:
        """只有系统分区时判 no-data，且不给出 kind 结论。"""
        topo_ = topo(disk("nvme0n1",
                          part("nvme0n1p1", "/", model.Role.SYSTEM)))
        found = layout.inspect(topo_)
        self.assertEqual(found.kind, layout.NONE)
        self.assertEqual(found.state, layout.State.NO_DATA)
        self.assertEqual(found.root, "nvme0n1")
        self.assertEqual(found.data, "")

    def test_no_root(self) -> None:
        topo_ = topo(disk("sda",
                          part("sda1", model.DATA_MOUNT, model.Role.DATA)))
        found = layout.inspect(topo_)
        self.assertEqual(found.state, layout.State.NO_ROOT)
        self.assertEqual(found.root, "")
        self.assertFalse(found.ok)

    def test_empty(self) -> None:
        found = layout.inspect(model.Topology(kind=model.TopoKind.EMPTY))
        self.assertEqual(found.state, layout.State.NO_ROOT)
        self.assertEqual(found.kind, layout.NONE)

    def test_not_last(self) -> None:
        """数据分区之后还有分区时，扩容会搬动系统分区，判 not-last。"""
        topo_ = topo(disk("nvme0n1",
                          part("nvme0n1p1", "/boot", model.Role.SYSTEM),
                          part("nvme0n1p2", model.DATA_MOUNT,
                               model.Role.DATA),
                          part("nvme0n1p3", "/", model.Role.SYSTEM)))
        found = layout.inspect(topo_)
        self.assertEqual(found.kind, layout.SINGLE)
        self.assertEqual(found.state, layout.State.NOT_LAST)
        self.assertFalse(found.ok)

    def test_swap_not_data(self) -> None:
        """盘尾是交换分区时，数据分区同样不能原地扩容。"""
        topo_ = topo(disk("nvme0n1",
                          part("nvme0n1p1", "/", model.Role.SYSTEM),
                          part("nvme0n1p2", model.DATA_MOUNT,
                               model.Role.DATA),
                          part("nvme0n1p3", None, model.Role.SWAP)))
        found = layout.inspect(topo_)
        self.assertEqual(found.state, layout.State.NOT_LAST)


class DumpTest(unittest.TestCase):
    """序列化：键名与 token 是外部契约，集合与取值都必须钉住。"""

    def test_keys(self) -> None:
        obj = layout.dump(layout.inspect(dual()))
        self.assertEqual(set(obj), {"kind", "verdict", "root", "data"})
        self.assertEqual(obj["verdict"], "ok")
        self.assertEqual(obj["kind"], "dual")

    def test_missing_token(self) -> None:
        obj = layout.dump(layout.inspect(topo()))
        self.assertEqual(obj["verdict"], "no-root")
        self.assertEqual(obj["kind"], "none")
        self.assertEqual(obj["root"], "")


if __name__ == "__main__":
    unittest.main()
