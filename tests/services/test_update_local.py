# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 本地版本身份采集测试
#
# 采集失败必须抛 Probe 而不是返回半份身份：命令层据异常给退出码，
# 部分结果会让「本机是谁」变成两个来源拼出来的猜测。

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyupdate import local  # noqa: E402


def result(code: int = 0, out: str = "", err: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=code, stdout=out, stderr=err)


class OsVersionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "os-release"

    def write(self, text: str) -> Path:
        self.path.write_text(text, encoding="utf-8")
        return self.path

    def test_reads_version(self) -> None:
        path = self.write('NAME=AirymaxOS\nVERSION_ID="0.0.4"\n')
        self.assertEqual(local.os_version(path), "0.0.4")

    def test_missing_file(self) -> None:
        with self.assertRaises(local.Probe):
            local.os_version(self.path)

    def test_missing_field(self) -> None:
        path = self.write("NAME=AirymaxOS\n")
        with self.assertRaises(local.Probe):
            local.os_version(path)

    def test_bad_value(self) -> None:
        path = self.write('VERSION_ID="0.0"\n')
        with self.assertRaises(local.Probe):
            local.os_version(path)


class RpmReleaseTest(unittest.TestCase):
    def test_returns_build(self) -> None:
        with mock.patch.object(local.subprocess, "run",
                               return_value=result(out="3\n")):
            self.assertEqual(local.rpm_release(), 3)

    def test_leading_zero(self) -> None:
        with mock.patch.object(local.subprocess, "run",
                               return_value=result(out="007")):
            self.assertEqual(local.rpm_release(), 7)

    def test_missing_package(self) -> None:
        with mock.patch.object(local.subprocess, "run",
                               return_value=result(1, err="not installed")):
            with self.assertRaises(local.Probe):
                local.rpm_release()

    def test_rpm_absent(self) -> None:
        with mock.patch.object(local.subprocess, "run",
                               side_effect=FileNotFoundError("rpm")):
            with self.assertRaises(local.Probe):
                local.rpm_release()

    def test_timeout(self) -> None:
        with mock.patch.object(local.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("rpm", 1)):
            with self.assertRaises(local.Probe):
                local.rpm_release()

    def test_non_numeric(self) -> None:
        with mock.patch.object(local.subprocess, "run",
                               return_value=result(out="rawhide")):
            with self.assertRaises(local.Probe):
                local.rpm_release()


class BaseSpanTest(unittest.TestCase):
    def test_returns_segment(self) -> None:
        with mock.patch.object(local.subprocess, "run",
                               return_value=result(out="24.03-72.oe2403sp3")):
            self.assertEqual(local.base_span(), 3)

    def test_without_segment(self) -> None:
        with mock.patch.object(local.subprocess, "run",
                               return_value=result(out="24.03-1")):
            self.assertIsNone(local.base_span())

    def test_missing_package(self) -> None:
        with mock.patch.object(local.subprocess, "run",
                               return_value=result(1, err="not installed")):
            self.assertIsNone(local.base_span())

    def test_rpm_absent(self) -> None:
        with mock.patch.object(local.subprocess, "run",
                               side_effect=FileNotFoundError("rpm")):
            self.assertIsNone(local.base_span())


class IdentityTest(unittest.TestCase):
    def test_triple(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "os-release"
            path.write_text('VERSION_ID="0.0.4"\n', encoding="utf-8")
            with mock.patch.object(local, "rpm_release", return_value=3), \
                    mock.patch.object(local.platform, "release",
                                      return_value="6.6.0-1.airy"):
                ident = local.local_identity(path)
        self.assertEqual(ident.version, "0.0.4")
        self.assertEqual(ident.build, 3)
        self.assertEqual(ident.kernel, "6.6.0-1.airy")

    def test_no_partial(self) -> None:
        with mock.patch.object(local, "os_version",
                               side_effect=local.Probe("no os-release")), \
                mock.patch.object(local, "rpm_release") as rpm:
            with self.assertRaises(local.Probe):
                local.local_identity()
        rpm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
