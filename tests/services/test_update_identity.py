# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 版本身份测试
#
# 覆盖三条判据：三段版本按数值比较（前导零不改变结论）、构建号前进
# 只判同版本重建、内核变化不参与判定。同时校验 os-release 解析只认
# VERSION_ID，缺字段即报错而不是猜一个默认值。

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyupdate import identity  # noqa: E402
from airyupdate.model import Identity  # noqa: E402


def ident(
    version: str = "0.0.4",
    build: int = 3,
    kernel: str = "6.6.0-alk",
) -> Identity:
    """构造身份三元组，默认取 0.0.4 的典型取值。"""
    return Identity(version, build, kernel)


class ParseVersionTest(unittest.TestCase):
    """版本号是数值不是字符串。"""

    def test_plain(self) -> None:
        self.assertEqual(identity.parse_version("0.0.4"), (0, 0, 4))

    def test_leading_zero(self) -> None:
        self.assertEqual(identity.parse_version("0.0.04"), (0, 0, 4))
        self.assertEqual(identity.parse_version("01.02.03"), (1, 2, 3))

    def test_numeric_order(self) -> None:
        self.assertGreater(
            identity.parse_version("0.0.10"),
            identity.parse_version("0.0.9"),
        )

    def test_rejects(self) -> None:
        for text in ("", "0.0", "0.0.4.1", "a.b.c", "0.0.-1", "0.0.4-3"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    identity.parse_version(text)


class ParseBuildTest(unittest.TestCase):
    """Release 的整数段即构建号。"""

    def test_plain(self) -> None:
        self.assertEqual(identity.parse_build("19"), 19)

    def test_leading_zero(self) -> None:
        self.assertEqual(identity.parse_build("0019"), 19)

    def test_suffix(self) -> None:
        self.assertEqual(identity.parse_build("22.alk"), 22)

    def test_rejects(self) -> None:
        for text in ("", "alk", "-3"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    identity.parse_build(text)


class VersionIdTest(unittest.TestCase):
    """os-release 只认 VERSION_ID。"""

    def read(self, body: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "os-release"
            path.write_text(body, encoding="utf-8")
            return identity.version_id(path)

    def test_quoted(self) -> None:
        self.assertEqual(
            self.read('NAME="AirymaxOS"\nVERSION_ID="0.0.4"\n'),
            "0.0.4",
        )

    def test_unquoted(self) -> None:
        self.assertEqual(self.read("VERSION_ID=0.0.4\n"), "0.0.4")

    def test_missing(self) -> None:
        with self.assertRaises(ValueError):
            self.read('PRETTY_NAME="AirymaxOS 0.0.4"\n')

    def test_empty(self) -> None:
        with self.assertRaises(ValueError):
            self.read('VERSION_ID=""\n')


class CompareTest(unittest.TestCase):
    """结论三态之外必须区分「同版本重建」。"""

    def test_same(self) -> None:
        self.assertIs(
            identity.compare(ident(), ident()),
            identity.Verdict.SAME,
        )

    def test_newer_version(self) -> None:
        self.assertIs(
            identity.compare(ident("0.0.4"), ident("0.0.5")),
            identity.Verdict.NEWER,
        )

    def test_older_version(self) -> None:
        self.assertIs(
            identity.compare(ident("0.0.5"), ident("0.0.4")),
            identity.Verdict.OLDER,
        )

    def test_rebuild(self) -> None:
        self.assertIs(
            identity.compare(ident(build=19), ident(build=22)),
            identity.Verdict.REBUILD,
        )

    def test_older_build(self) -> None:
        self.assertIs(
            identity.compare(ident(build=22), ident(build=19)),
            identity.Verdict.OLDER,
        )

    def test_kernel_ignored(self) -> None:
        self.assertIs(
            identity.compare(ident(kernel="6.6.0-alk"), ident(kernel="6.6.1")),
            identity.Verdict.SAME,
        )

    def test_leading_zero_equal(self) -> None:
        self.assertIs(
            identity.compare(ident("0.0.04"), ident("0.0.4")),
            identity.Verdict.SAME,
        )


if __name__ == "__main__":
    unittest.main()
