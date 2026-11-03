# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 基础挂载判定脚本行为回归测试
#
# 在临时文件上构造 mountinfo，验证根挂载状态、/boot 与 ESP 是否独立挂载
# 三个事实，以及只读根按 note 上报的三档退出码契约。

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import units_lint  # noqa: E402

SCRIPT = ROOT / "scripts" / "boot_mount.sh"

ROOT_RW = "36 35 0:32 / / rw,relatime - ext4 /dev/sda2 rw\n"
ROOT_RO = "36 35 0:32 / / ro,relatime - ext4 /dev/sda2 ro\n"
BOOT = "40 35 0:33 / /boot rw,relatime - ext4 /dev/sda3 rw\n"


class MountCase(unittest.TestCase):
    """构造隔离的挂载表并驱动脚本。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.info = Path(self.tmp.name) / "mountinfo"
        self.esp = "/boot/efi"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def table(self, *rows: str) -> None:
        self.info.write_text("".join(rows), encoding="utf-8")

    def esp_row(self, path: str) -> str:
        return f"44 35 0:34 / {path} rw,relatime - vfat /dev/sda1 rw\n"

    def run_check(self) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, AIRY_MOUNTINFO=str(self.info),
                   AIRY_ESP=self.esp)
        return subprocess.run(
            ["bash", str(SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_rw_root_with_both_mounts(self) -> None:
        self.table(ROOT_RW, BOOT, self.esp_row(self.esp))
        out = self.run_check()
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout,
                         "root=rw boot=mount efi=mount\n")

    def test_ro_root_is_a_note(self) -> None:
        self.table(ROOT_RO)
        out = self.run_check()
        self.assertEqual(out.returncode, 3)
        self.assertEqual(out.stdout, "root=ro boot=dir efi=dir\n")

    def test_boot_only(self) -> None:
        self.table(ROOT_RW, BOOT)
        out = self.run_check()
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout,
                         "root=rw boot=mount efi=dir\n")

    def test_missing_table_fails(self) -> None:
        out = self.run_check()
        self.assertEqual(out.returncode, 1)
        self.assertEqual(out.stdout, "")
        self.assertIn("读不到挂载表", out.stderr)

    def test_missing_root_fails(self) -> None:
        self.table(BOOT)
        out = self.run_check()
        self.assertEqual(out.returncode, 1)
        self.assertEqual(out.stdout,
                         "root=unknown boot=mount efi=dir\n")

    def test_escaped_mount_point(self) -> None:
        self.esp = "/boot/my efi"
        self.table(ROOT_RW, BOOT, self.esp_row("/boot/my\\040efi"))
        out = self.run_check()
        self.assertEqual(out.returncode, 0)
        self.assertIn("efi=mount", out.stdout)

    def test_lint_ok(self) -> None:
        self.assertEqual(units_lint.check_script(SCRIPT), [])


if __name__ == "__main__":
    unittest.main()
