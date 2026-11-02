# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# RT 带宽测试共用的临时机器
#
# 判定层与命令行层需要同一棵「带内核接口与 cgroup 目录的临时机器」。把它
# 收在一处，两个测试文件不再各抄一份目录拼装逻辑，测试树本身也不会因为
# 抄漏某个文件而让两条用例悄悄测不同的东西。

"""Shared temporary machine for the RT bandwidth tests (X-01)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from airymax_rt import cgroup, policy

MOUNT = "cpu,cpuacct"


class Tree(unittest.TestCase):
    """一棵临时机器：默认带可读的 sysctl 接口，cgroup 树按用例搭建。"""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name)
        self.policy()

    def policy(self, total: str = "950000\n", period: str = "1000000\n"):
        total_path = self.base / "runtime"
        period_path = self.base / "period"
        total_path.write_text(total, encoding="ascii")
        period_path.write_text(period, encoding="ascii")
        return policy.Policy(
            total_path=str(total_path),
            period_path=str(period_path),
            cgroup_root=str(self.base),
        )

    def mount(self) -> Path:
        mount = self.base / MOUNT
        mount.mkdir()
        (mount / cgroup.V1_FILE).write_text("950000\n", encoding="ascii")
        return mount

    def slice(self, mount: Path, name: str, value: int) -> None:
        path = mount / name
        path.mkdir()
        (path / cgroup.V1_FILE).write_text(f"{value}\n", encoding="ascii")

    def all_slices(self, value: int = 190000) -> Path:
        mount = self.mount()
        for name in policy.DEFAULT_SLICES:
            self.slice(mount, name, value)
        return mount

    def args(self, cmd: str) -> list[str]:
        """把子命令接到本机临时路径上。"""
        return [
            cmd,
            "--proc",
            str(self.base / "runtime"),
            "--period",
            str(self.base / "period"),
            "--cgroup",
            str(self.base),
        ]
