# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# deb 测试样本构造
#
# ar 头由真实工具写，tar 成员由标准库写：这样 ar 解析测的是真格式，同时
# 能构造绝对路径、越界链接、设备节点这类 tar 命令行不肯生成的恶意成员。
# 仓库不提交任何二进制样本，每个用例现场生成。

"""On-the-fly deb samples for the airy-deb tests."""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

CONTROL = """Package: {package}
Version: {version}
Architecture: {arch}
Maintainer: Airy <airy@example.com>
{extra}Description: demo package
 A long description line.
"""


def regular(name: str, data: bytes, mode: int = 0o644):
    """一个常规文件成员。"""
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    return info, io.BytesIO(data)


def symlink(name: str, target: str):
    """一个符号链接成员。"""
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    return info, None


def hardlink(name: str, target: str):
    """一个硬链接成员。"""
    info = tarfile.TarInfo(name)
    info.type = tarfile.LNKTYPE
    info.linkname = target
    return info, None


def node(name: str, kind: bytes):
    """一个设备节点或管道成员。"""
    info = tarfile.TarInfo(name)
    info.type = kind
    return info, None


def sample():
    """默认的 data 归档内容：一个可执行文件与一份文档。"""
    return [
        regular("usr/bin/demo", b"#!/bin/sh\necho demo\n", 0o755),
        regular("usr/share/doc/demo/copyright", b"MIT\n"),
    ]


def tarball(path: Path, entries, comp: str = "gz") -> Path:
    """按条目列表写一个压缩 tar；zst 写成裸 tar，解压由用例模拟。"""
    with tarfile.open(path, {"gz": "w:gz", "xz": "w:xz",
                             "zst": "w"}[comp]) as tar:
        for info, data in entries:
            tar.addfile(info, data)
    return path


class Tree(unittest.TestCase):
    """每个用例一份临时目录，deb 样本全部现场生成。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.n = 0

    def deb(self, *, package: str = "demo", version: str = "1.0-1",
            arch: str = "amd64", depends: str = "",
            recommends: str = "", binary: str = "2.0\n",
            entries=None, scripts=None, comp: str = "gz",
            first: bool = True) -> Path:
        """生成一个可被 airy-deb 读到的 deb，返回其路径。"""
        if shutil.which("ar") is None:
            self.skipTest("缺少 ar")
        self.n += 1
        stage = self.base / f"deb{self.n}"
        stage.mkdir()
        control_dir = stage / "control"
        control_dir.mkdir()
        (control_dir / "control").write_text(
            _control(package, version, arch, depends, recommends))
        for name, body in (scripts or {}).items():
            path = control_dir / name
            path.write_text(body)
            path.chmod(0o755)
        parts = [
            _plain(stage / "debian-binary", binary),
            tarball(stage / f"control.tar.{comp}", _control_entries(control_dir),
                    comp),
            tarball(stage / f"data.tar.{comp}", entries or sample(), comp),
        ]
        out = stage / "sample.deb"
        subprocess.run(
            ["ar", "rc", str(out), *[str(item) for item in
                                    (parts if first else reversed(parts))]],
            check=True, cwd=stage)
        return out


def _control(package: str, version: str, arch: str, depends: str,
             recommends: str) -> str:
    extra = ""
    if depends:
        extra += f"Depends: {depends}\n"
    if recommends:
        extra += f"Recommends: {recommends}\n"
    return CONTROL.format(package=package, version=version, arch=arch,
                          extra=extra)


def _plain(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


def _control_entries(folder: Path):
    return [
        regular("./" + path.name, path.read_bytes(),
                path.stat().st_mode & 0o777)
        for path in sorted(folder.iterdir())
    ]
