# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 安装期选盘记录比对回归测试
#
# 记录由安装器写入、由 airy-disk 读取，两侧的 schema 只能靠契约对齐；
# 本文件把「缺记录、坏记录、换盘、改容量、未知总线」五种现场钉成断言，
# 避免判定随实现漂移成自由文本。

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airydisk import model, record  # noqa: E402

GiB = 1024**3


def topo() -> model.Topology:
    disk = model.Disk(
        name="nvme0n1", size=512 * GiB, bus=model.Bus.NVME,
        table=model.TableKind.GPT,
    )
    return model.Topology(kind=model.TopoKind.SINGLE, disks=(disk,))


def install(**over: object) -> record.Install:
    base = dict(state="ok", disk="nvme0n1", size=512 * GiB, bus="nvme",
                firmware="uefi", layout="single", medium="sda")
    base.update(over)
    return record.Install(**base)  # type: ignore[arg-type]


class LoadTest(unittest.TestCase):
    """记录读取：缺失、损坏与 schema 不符分别是三种事实。"""

    def write(self, obj: object) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "install-disk.json"
        path.write_text(json.dumps(obj), encoding="utf-8")
        return path

    def test_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            got = record.load(str(Path(tmp) / "none.json"))
        self.assertEqual(got.state, "absent")
        self.assertFalse(got.known)

    def test_valid(self) -> None:
        path = self.write({"schema": 1, "disk": "nvme0n1", "bytes": 512 * GiB,
                           "bus": "nvme", "firmware": "uefi",
                           "layout": "split", "medium": ""})
        got = record.load(str(path))
        self.assertTrue(got.known)
        self.assertEqual(got.disk, "nvme0n1")
        self.assertEqual(got.size, 512 * GiB)
        self.assertEqual(got.layout, "split")

    def test_broken_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(record.load(str(path)).state, "invalid")

    def test_schema_mismatch(self) -> None:
        path = self.write({"schema": 2, "disk": "nvme0n1"})
        self.assertEqual(record.load(str(path)).state, "invalid")

    def test_partial(self) -> None:
        """缺字段按空值处理，不让半份记录把比对整体判负。"""
        path = self.write({"schema": 1, "disk": "nvme0n1"})
        got = record.load(str(path))
        self.assertTrue(got.known)
        self.assertEqual(got.size, 0)
        self.assertEqual(got.bus, "")


class CompareTest(unittest.TestCase):
    """比对只看盘名、容量与已知总线。"""

    def test_match(self) -> None:
        agree = record.compare(install(), topo())
        self.assertTrue(agree.ok)
        self.assertEqual(agree.diff, ())

    def test_absent(self) -> None:
        got = record.compare(record.Install(state="absent"), topo())
        self.assertIs(got.verdict, record.Verdict.ABSENT)

    def test_invalid(self) -> None:
        got = record.compare(record.Install(state="invalid"), topo())
        self.assertIs(got.verdict, record.Verdict.INVALID)

    def test_disk_missing(self) -> None:
        got = record.compare(install(disk="sda"), topo())
        self.assertIs(got.verdict, record.Verdict.MISMATCH)
        self.assertEqual(got.diff, ("disk",))

    def test_size(self) -> None:
        got = record.compare(install(size=1024 * GiB), topo())
        self.assertEqual(got.diff, ("size",))

    def test_bus(self) -> None:
        got = record.compare(install(bus="sata"), topo())
        self.assertEqual(got.diff, ("bus",))

    def test_unknown_bus_skips(self) -> None:
        """安装器记录的总线未知（-）时不比对总线。"""
        got = record.compare(install(bus=record.UNKNOWN_BUS), topo())
        self.assertTrue(got.ok)

    def test_dump(self) -> None:
        obj = record.dump(install(layout="split"),
                          record.compare(install(), topo()))
        self.assertEqual(obj["verdict"], "match")
        self.assertEqual(obj["layout"], "split")
        self.assertEqual(obj["diff"], [])


if __name__ == "__main__":
    unittest.main()
