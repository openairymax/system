# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# archive 单测
#
# 覆盖 ar 结构校验、三种压缩中的 gz/xz、data 成员统计与解包，以及解包前的
# 路径判死：绝对路径、.. 越界、符号链接与硬链接逃逸、设备节点都必须报错，
# 且失败时目标目录没有落下一部分内容。

"""Tests for the airydeb archive reader."""

from __future__ import annotations

import os
import shutil
import sys
import tarfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from airydeb import archive, err  # noqa: E402
from deb_fixture import (  # noqa: E402
    Tree,
    hardlink,
    node,
    regular,
    sample,
    symlink,
)


def _passthrough(_argv, stdin, stdout, check):
    shutil.copyfileobj(stdin, stdout)


class OpenTest(Tree):
    def test_reads_control(self):
        deb = archive.open_deb(self.deb())
        self.assertEqual(deb.data.name, "data.tar.gz")
        self.assertIn("Package: demo", archive.control_text(deb))

    def test_rejects_reordered(self):
        with self.assertRaises(err.Fault):
            archive.open_deb(self.deb(first=False))

    def test_rejects_plain_file(self):
        path = self.base / "plain.deb"
        path.write_text("not a deb\n")
        with self.assertRaises(err.Fault):
            archive.open_deb(path)

    def test_rejects_wrong_binary(self):
        with self.assertRaises(err.Fault):
            archive.open_deb(self.deb(binary="3.0\n"))

    def test_reads_scripts(self):
        path = self.deb(scripts={"postinst": "#!/bin/sh\nexit 0\n"})
        found = archive.scripts(archive.open_deb(path))
        self.assertEqual([item.name for item in found], ["postinst"])
        self.assertIn("exit 0", found[0].text)

    def test_scripts_order(self):
        scripts = {"postrm": "a\n", "preinst": "b\n"}
        found = archive.scripts(archive.open_deb(self.deb(scripts=scripts)))
        self.assertEqual([item.name for item in found], ["preinst", "postrm"])


class ScanTest(Tree):
    def test_counts_members(self):
        count, size = archive.scan(archive.open_deb(self.deb()))
        self.assertEqual(count, 2)
        self.assertEqual(size, len(b"#!/bin/sh\necho demo\n") + len(b"MIT\n"))

    def test_reads_xz(self):
        self.assertEqual(archive.scan(archive.open_deb(self.deb(comp="xz")))[0],
                         2)


class UnpackTest(Tree):
    def test_writes_tree(self):
        dest = self.base / "root"
        archive.unpack(archive.open_deb(self.deb()), dest)
        binary = dest / "usr/bin/demo"
        self.assertTrue(binary.is_file())
        self.assertTrue(os.access(binary, os.X_OK))
        doc = dest / "usr/share/doc/demo/copyright"
        self.assertEqual(doc.read_text(), "MIT\n")

    def test_writes_xz(self):
        dest = self.base / "root"
        archive.unpack(archive.open_deb(self.deb(comp="xz")), dest)
        self.assertTrue((dest / "usr/bin/demo").is_file())


class ZstTest(Tree):
    """zst 走 zstd -d；用例替换子进程，不依赖本机是否装了 zstd。"""

    def test_reads_zst(self):
        path = self.deb(comp="zst")
        with mock.patch("airydeb.archive.subprocess.run") as run:
            run.side_effect = _passthrough
            count, _ = archive.scan(archive.open_deb(path))
        self.assertEqual(count, 2)

    def test_requires_zstd(self):
        path = self.deb(comp="zst")
        with mock.patch("airydeb.archive.subprocess.run") as run:
            run.side_effect = FileNotFoundError
            deb = archive.open_deb(path)
            with self.assertRaises(err.Fault) as caught:
                archive.scan(deb)
        self.assertIn("zstd", str(caught.exception))


class SafetyTest(Tree):
    def check(self, entries):
        dest = self.base / "root"
        with self.assertRaises(err.Fault):
            archive.unpack(archive.open_deb(self.deb(entries=entries)), dest)
        self.assertFalse(dest.exists())

    def test_rejects_absolute(self):
        self.check([regular("/etc/evil", b"x")])

    def test_rejects_dotdot(self):
        self.check([regular("../evil", b"x")])

    def test_rejects_abs_symlink(self):
        self.check(sample() + [symlink("usr/lib/evil", "/etc/passwd")])

    def test_rejects_symlink_escape(self):
        self.check(sample() + [symlink("usr/lib/evil", "../../../etc/passwd")])

    def test_rejects_hardlink_escape(self):
        self.check(sample() + [hardlink("usr/bin/evil", "../../etc/passwd")])

    def test_rejects_device(self):
        self.check(sample() + [node("dev/null", tarfile.CHRTYPE)])

    def test_rejects_fifo(self):
        self.check(sample() + [node("run/fifo", tarfile.FIFOTYPE)])


if __name__ == "__main__":
    unittest.main()
