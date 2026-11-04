# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# RT 带宽 cgroup 布局回归测试
#
# 用临时目录模拟 v1 与 v2 两种记账树：v1 靠反查 cpu.rt_runtime_us 定位
# 挂载点，v2 靠 cgroup.controllers 与 cpu.rt.max 判定是否可记账。同时验证
# 写入后回读、越界目录自动创建、以及 v2 根分发 cpu 控制器。

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airymax_rt import cgroup  # noqa: E402


class TempTree(unittest.TestCase):
    """提供一棵可写的临时记账树。"""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name)

    def v1(self) -> cgroup.Layout:
        mount = self.base / "cpu,cpuacct"
        mount.mkdir()
        (mount / cgroup.V1_FILE).write_text("950000\n", encoding="ascii")
        found = cgroup.detect(str(self.base))
        assert found is not None
        return found

    def v2(self, controllers: str = "cpu io memory") -> cgroup.Layout:
        (self.base / cgroup.CONTROLLER).write_text(
            controllers + "\n", encoding="ascii"
        )
        (self.base / cgroup.V2_FILE).write_text(
            "max 1000000\n", encoding="ascii"
        )
        (self.base / cgroup.SUBTREE).write_text("memory\n", encoding="ascii")
        found = cgroup.detect(str(self.base))
        assert found is not None
        return found


class DetectTest(TempTree):
    """布局识别的分支覆盖。"""

    def test_v1(self) -> None:
        found = self.v1()
        self.assertIs(found.kind, cgroup.Kind.V1)
        self.assertEqual(found.file, cgroup.V1_FILE)
        self.assertTrue(found.supported)
        self.assertTrue(found.root.endswith("cpu,cpuacct"))

    def test_v2(self) -> None:
        found = self.v2()
        self.assertIs(found.kind, cgroup.Kind.V2)
        self.assertEqual(found.file, cgroup.V2_FILE)
        self.assertTrue(found.supported)

    def test_v2_without_rt_file(self) -> None:
        (self.base / cgroup.CONTROLLER).write_text("cpu memory\n", "ascii")
        found = cgroup.detect(str(self.base))
        assert found is not None
        self.assertIs(found.kind, cgroup.Kind.V2)
        self.assertFalse(found.supported)

    def test_v2_without_cpu(self) -> None:
        (self.base / cgroup.CONTROLLER).write_text("io memory\n", "ascii")
        found = cgroup.detect(str(self.base))
        assert found is not None
        self.assertIs(found.kind, cgroup.Kind.V2)
        self.assertFalse(found.supported)
        self.assertFalse(found.routed)

    def test_empty_root(self) -> None:
        self.assertIsNone(cgroup.detect(str(self.base)))

    def test_missing_root(self) -> None:
        self.assertIsNone(cgroup.detect(str(self.base / "absent")))


class WriteTest(TempTree):
    """写入与回读。"""

    def test_v1_creates_slice(self) -> None:
        found = self.v1()
        self.assertTrue(cgroup.write(found, "user.slice", 190000, 1000000))
        path = cgroup.target(found, "user.slice") / cgroup.V1_FILE
        self.assertEqual(path.read_text(encoding="ascii").strip(), "190000")
        self.assertEqual(cgroup.read(found, "user.slice"), 190000)

    def test_v1_existing_slice(self) -> None:
        found = self.v1()
        (cgroup.target(found, "system.slice")).mkdir()
        self.assertTrue(cgroup.write(found, "system.slice", 1, 1000000))

    def test_v2_writes_pair(self) -> None:
        found = self.v2()
        self.assertTrue(cgroup.write(found, "airymax.slice", 190000, 1000000))
        path = cgroup.target(found, "airymax.slice") / cgroup.V2_FILE
        text = path.read_text(encoding="ascii").strip()
        self.assertEqual(text, "190000 1000000")
        self.assertEqual(cgroup.read(found, "airymax.slice"), 190000)

    def test_v2_distributes_cpu(self) -> None:
        found = self.v2()
        cgroup.write(found, "user.slice", 190000, 1000000)
        text = (self.base / cgroup.SUBTREE).read_text(encoding="ascii")
        tokens = {item.lstrip("+-") for item in text.split()}
        self.assertIn("cpu", tokens)

    def test_v2_without_rt_file_refuses(self) -> None:
        (self.base / cgroup.CONTROLLER).write_text("cpu memory\n", "ascii")
        found = cgroup.detect(str(self.base))
        assert found is not None
        self.assertFalse(cgroup.write(found, "user.slice", 190000, 1000000))

    def test_v2_without_subtree_refuses(self) -> None:
        found = self.v2()
        (self.base / cgroup.SUBTREE).unlink()
        self.assertFalse(cgroup.write(found, "user.slice", 190000, 1000000))

    def test_zero_budget_refused(self) -> None:
        found = self.v1()
        self.assertFalse(cgroup.write(found, "user.slice", 0, 1000000))

    def test_v2_max_reads_none(self) -> None:
        found = self.v2()
        path = cgroup.target(found, "user.slice")
        path.mkdir()
        (path / cgroup.V2_FILE).write_text("max 1000000\n", encoding="ascii")
        self.assertIsNone(cgroup.read(found, "user.slice"))

    def test_read_missing(self) -> None:
        self.assertIsNone(cgroup.read(self.v1(), "absent.slice"))


class RawTest(TempTree):
    """原始值与子组枚举：判定侧要靠它们区分 0、-1 与 max。"""

    def test_v1_raw(self) -> None:
        found = self.v1()
        self.assertEqual(cgroup.read_raw(found, ""), "950000")
        self.assertEqual(cgroup.read(found, ""), 950000)

    def test_v1_unlimited(self) -> None:
        found = self.v1()
        path = cgroup.target(found, "user.slice")
        path.mkdir()
        (path / cgroup.V1_FILE).write_text("-1\n", encoding="ascii")
        self.assertEqual(
            cgroup.read_raw(found, "user.slice"), cgroup.UNLIMITED
        )
        self.assertIsNone(cgroup.read(found, "user.slice"))

    def test_v2_raw(self) -> None:
        found = self.v2()
        self.assertEqual(cgroup.read_raw(found, ""), cgroup.MAX)
        self.assertIsNone(cgroup.read(found, ""))

    def test_raw_missing(self) -> None:
        self.assertIsNone(cgroup.read_raw(self.v1(), "absent.slice"))

    def test_subgroups_depth(self) -> None:
        found = self.v1()
        parent = cgroup.target(found, "user.slice")
        for name in ("user-1000.slice", "user-1001.slice"):
            (parent / name).mkdir(parents=True)
        self.assertEqual(
            cgroup.subgroups(found, "user.slice", 1),
            ("user.slice/user-1000.slice", "user.slice/user-1001.slice"),
        )
        self.assertEqual(cgroup.subgroups(found, "", 1), ("user.slice",))

    def test_subgroups_nested(self) -> None:
        found = self.v1()
        leaf = cgroup.target(found, "user.slice/user-1000.slice/a.scope")
        leaf.mkdir(parents=True)
        self.assertEqual(
            cgroup.subgroups(found, "", 3),
            (
                "user.slice",
                "user.slice/user-1000.slice",
                "user.slice/user-1000.slice/a.scope",
            ),
        )

    def test_subgroups_zero_depth(self) -> None:
        self.assertEqual(cgroup.subgroups(self.v1(), "", 0), ())


if __name__ == "__main__":
    unittest.main()
