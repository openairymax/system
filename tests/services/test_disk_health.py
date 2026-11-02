# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 数据分区健康判定回归测试
#
# statvfs 与 ismount 全部以固定值注入，不读本机文件系统。覆盖健康、
# 低空间、只读、未挂载、无数据分区、读取失败与零容量七种现场，核对
# 判定 token、容量换算与建议里的可执行命令。

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airydisk import health, model  # noqa: E402

BLOCK = 1024


def vfs(blocks: int = 1000, bfree: int = 500, bavail: int = 450,
        flag: int = 0) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        f_blocks=blocks, f_bfree=bfree, f_bavail=bavail,
        f_frsize=BLOCK, f_flag=flag,
    )


def topo(fs: model.FsKind = model.FsKind.EXT4,
         data: bool = True) -> model.Topology:
    parts = [model.Partition(name="vda1", size=1024,
                             fs=model.FsKind.EXT4, mount="/",
                             role=model.Role.SYSTEM)]
    if data:
        parts.append(model.Partition(name="vda2", size=2048, fs=fs,
                                     mount=model.DATA_MOUNT,
                                     role=model.Role.DATA))
    disk = model.Disk(name="vda", size=4096, bus=model.Bus.VIRTIO,
                      table=model.TableKind.GPT, parts=tuple(parts))
    return model.Topology(kind=model.TopoKind.SINGLE, disks=(disk,))


class Case(unittest.TestCase):
    """注入挂载事实与容量，避免依赖测试机。"""

    def inspect(self, topo: model.Topology, view: object | None = None,
                mounted: bool = True) -> health.Health:
        with mock.patch("airydisk.health.ismount", return_value=mounted):
            with mock.patch("airydisk.health.statvfs",
                            return_value=view or vfs()):
                return health.inspect(topo)


class StateTest(Case):
    """判定顺序：挂载事实先于容量，只读先于低空间。"""

    def test_ok(self) -> None:
        found = self.inspect(topo())
        self.assertEqual(found.state, health.State.OK)
        self.assertTrue(found.ok)
        self.assertEqual(found.part, "vda2")
        self.assertEqual(found.total, 1000 * BLOCK)
        self.assertEqual(found.free, 450 * BLOCK)
        self.assertEqual(found.used, 52)
        self.assertEqual(found.advice, "")

    def test_low_space(self) -> None:
        found = self.inspect(topo(), vfs(bfree=100, bavail=50))
        self.assertEqual(found.state, health.State.LOW_SPACE)
        self.assertEqual(found.used, 94)
        self.assertIn("airy-disk grow", found.advice)

    def test_threshold(self) -> None:
        """恰好在阈值上就判低空间，判定边界不留给下游解释。"""
        found = self.inspect(topo(), vfs(bfree=100, bavail=100))
        self.assertEqual(found.used, health.LIMIT)
        self.assertEqual(found.state, health.State.LOW_SPACE)

    def test_read_only(self) -> None:
        """只读同时低空间时先报只读：先修文件系统，再谈扩容。"""
        found = self.inspect(topo(), vfs(bfree=100, bavail=10,
                                         flag=1))
        self.assertEqual(found.state, health.State.READ_ONLY)
        self.assertIn("remount,rw", found.advice)

    def test_no_data(self) -> None:
        found = self.inspect(topo(data=False))
        self.assertEqual(found.state, health.State.NO_DATA)
        self.assertIn("/data", found.advice)

    def test_unmounted(self) -> None:
        """拓扑有记录而内核未挂载时不拿根分区容量冒充数据分区。"""
        found = self.inspect(topo(), mounted=False)
        self.assertEqual(found.state, health.State.UNAVAILABLE)
        self.assertEqual(found.total, 0)

    def test_read_error(self) -> None:
        with mock.patch("airydisk.health.ismount", return_value=True):
            with mock.patch("airydisk.health.statvfs",
                            side_effect=OSError("EIO")):
                found = health.inspect(topo())
        self.assertEqual(found.state, health.State.UNAVAILABLE)

    def test_zero_capacity(self) -> None:
        found = self.inspect(topo(), vfs(blocks=0, bfree=0, bavail=0))
        self.assertEqual(found.state, health.State.UNAVAILABLE)


class RemedyTest(Case):
    """建议必须指向具体动作与具体设备，不能只给结论。"""

    def test_unavailable_names_device(self) -> None:
        found = self.inspect(topo(), mounted=False)
        self.assertIn("fsck.ext4 -f /dev/vda2", found.advice)

    def test_xfs_check(self) -> None:
        found = self.inspect(topo(model.FsKind.XFS), mounted=False)
        self.assertIn("xfs_repair -n /dev/vda2", found.advice)


class DumpTest(Case):
    """JSON 形态供系统健康巡检与门禁直接判读。"""

    def test_keys(self) -> None:
        obj = health.dump(self.inspect(topo()))
        self.assertEqual(obj["verdict"], "ok")
        self.assertEqual(obj["part"], "vda2")
        self.assertEqual(obj["total"], 1000 * BLOCK)
        self.assertEqual(obj["used"], 52)
        self.assertIn("advice", obj)

    def test_no_data(self) -> None:
        obj = health.dump(self.inspect(topo(data=False)))
        self.assertEqual(obj["verdict"], "no-data")
        self.assertEqual(obj["part"], "")


if __name__ == "__main__":
    unittest.main()
