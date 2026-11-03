# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 数据分区挂载恢复回归测试
#
# 判定只读 fstab 文本与挂载事实，执行用假执行器注入结果，不调用 mount。
# 覆盖已挂载、待挂载、缺记录、noauto、设备名与执行失败六种现场，核对
# 判定 token、挂载命令与建议中的可执行动作。

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airydisk import mount, ops, runner  # noqa: E402

FSTAB = (
    "UUID=1111-2222 / ext4 defaults 0 1\n"
    "UUID=3333-4444 /data ext4 defaults 0 2\n"
)

NO_ENTRY = "UUID=1111-2222 / ext4 defaults 0 1\n"

NOAUTO = (
    "UUID=1111-2222 / ext4 defaults 0 1\n"
    "UUID=3333-4444 /data ext4 noauto 0 2\n"
)

UNSTABLE = (
    "UUID=1111-2222 / ext4 defaults 0 1\n"
    "/dev/vda2 /data ext4 defaults 0 2\n"
)


def passed(step: ops.Step, timeout: float) -> runner.Result:
    return runner.Result(step=step, rc=0)


def failed(rc: int):
    def call(step: ops.Step, timeout: float) -> runner.Result:
        return runner.Result(step=step, rc=rc, err="mount failed")

    return call


class JudgeTest(unittest.TestCase):
    """判定顺序：先认已挂载，再判 fstab 能否安全挂载。"""

    def test_mounted(self) -> None:
        found = mount.judge(FSTAB, mounted=True)
        self.assertEqual(found.state, mount.State.MOUNTED)
        self.assertTrue(found.ok)
        self.assertIsNone(found.step)

    def test_ready(self) -> None:
        found = mount.judge(FSTAB, mounted=False)
        self.assertEqual(found.state, mount.State.READY)
        self.assertEqual(found.source, "UUID=3333-4444")
        self.assertEqual(found.step.argv, (mount.MOUNT, "/data"))
        self.assertFalse(found.ok)

    def test_no_entry(self) -> None:
        found = mount.judge(NO_ENTRY, mounted=False)
        self.assertEqual(found.state, mount.State.NO_ENTRY)
        self.assertIsNone(found.step)
        self.assertIn("blkid", found.advice)

    def test_unset(self) -> None:
        """没有选盘记录的系统是旧布局，缺 /data 记录属未启用而非失败。"""
        found = mount.judge(NO_ENTRY, mounted=False, declared=False)
        self.assertEqual(found.state, mount.State.UNSET)
        self.assertIsNone(found.step)
        self.assertFalse(found.ok)
        self.assertIn("未启用", found.advice)

    def test_noauto(self) -> None:
        found = mount.judge(NOAUTO, mounted=False)
        self.assertEqual(found.state, mount.State.NOAUTO)
        self.assertIsNone(found.step)
        self.assertIn("noauto", found.advice)

    def test_unstable(self) -> None:
        """设备名记录可能挂到改号后的别的文件系统，不执行挂载。"""
        found = mount.judge(UNSTABLE, mounted=False)
        self.assertEqual(found.state, mount.State.UNSTABLE)
        self.assertEqual(found.source, "/dev/vda2")
        self.assertIsNone(found.step)
        self.assertIn("UUID", found.advice)


class EnsureTest(unittest.TestCase):
    """执行只在待挂载时发生，失败必须带回退出码与下一步。"""

    def ensure(self, text: str, mounted: bool,
               func=passed, declared: bool = True
               ) -> tuple[mount.Mount, list[ops.Step]]:
        calls: list[ops.Step] = []

        def spy(step: ops.Step, timeout: float) -> runner.Result:
            calls.append(step)
            return func(step, timeout)

        with mock.patch("airydisk.mount.ismount", return_value=mounted):
            return mount.ensure(text, spy, declared=declared), calls

    def test_mounted_skips(self) -> None:
        found, calls = self.ensure(FSTAB, mounted=True)
        self.assertEqual(found.state, mount.State.MOUNTED)
        self.assertEqual(calls, [])

    def test_restored(self) -> None:
        found, calls = self.ensure(FSTAB, mounted=False)
        self.assertEqual(found.state, mount.State.RESTORED)
        self.assertTrue(found.ok)
        self.assertEqual(len(calls), 1)

    def test_failed(self) -> None:
        found, _ = self.ensure(FSTAB, mounted=False, func=failed(32))
        self.assertEqual(found.state, mount.State.FAILED)
        self.assertEqual(found.rc, 32)
        self.assertIn("mount -a", found.advice)

    def test_refused_skips(self) -> None:
        found, calls = self.ensure(NOAUTO, mounted=False)
        self.assertEqual(found.state, mount.State.NOAUTO)
        self.assertEqual(calls, [])

    def test_unset_skips(self) -> None:
        found, calls = self.ensure(NO_ENTRY, mounted=False, declared=False)
        self.assertEqual(found.state, mount.State.UNSET)
        self.assertEqual(calls, [])


class DumpTest(unittest.TestCase):
    """JSON 形态供单元与巡检直接判读。"""

    def test_keys(self) -> None:
        obj = mount.dump(mount.judge(FSTAB, mounted=False))
        self.assertEqual(obj["verdict"], "ready")
        self.assertEqual(obj["source"], "UUID=3333-4444")
        self.assertEqual(obj["rc"], 0)
        self.assertIn("advice", obj)


if __name__ == "__main__":
    unittest.main()
