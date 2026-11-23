# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# charset 判定与计数测试
#
# 每一类坏序列各一条最小用例，覆盖 §2.2 的判定顺序：首字节、续字节、过长、
# 代理对、上界、非字符、BOM。合法侧只留三类：ASCII、多字节 CJK、四字节表情。

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airycharset import scan  # noqa: E402


class Valid(unittest.TestCase):
    def test_ascii(self) -> None:
        self.assertIsNone(scan.validate(b"hello\n"))

    def test_cjk(self) -> None:
        self.assertIsNone(scan.validate("中文界面".encode()))

    def test_emoji(self) -> None:
        self.assertIsNone(scan.validate("ok \U0001F600".encode()))

    def test_empty(self) -> None:
        self.assertIsNone(scan.validate(b""))


class Bad(unittest.TestCase):
    def code(self, data: bytes) -> str:
        issue = scan.validate(data)
        assert issue is not None
        return issue.code

    def test_bad_lead(self) -> None:
        self.assertEqual(self.code(b"\xff"), "invalid")

    def test_truncated(self) -> None:
        self.assertEqual(self.code(b"\xe4\xb8"), "invalid")

    def test_bad_cont(self) -> None:
        self.assertEqual(self.code(b"\xe4\x41\xad"), "invalid")

    def test_overlong(self) -> None:
        self.assertEqual(self.code(b"\xe0\x80\x80"), "overlong")

    def test_overlong4(self) -> None:
        self.assertEqual(self.code(b"\xf0\x80\x80\x80"), "overlong")

    def test_surrogate(self) -> None:
        self.assertEqual(self.code(b"\xed\xa0\x80"), "surrogate")

    def test_range(self) -> None:
        self.assertEqual(self.code(b"\xf4\x90\x80\x80"), "range")

    def test_nonchar(self) -> None:
        self.assertEqual(self.code(b"\xef\xb7\x90"), "nonchar")

    def test_plane_end(self) -> None:
        self.assertEqual(self.code(b"\xef\xbf\xbe"), "nonchar")

    def test_lead_bom(self) -> None:
        self.assertEqual(self.code(b"\xef\xbb\xbfa"), "bom")

    def test_inner_bom(self) -> None:
        self.assertEqual(self.code(b"a\xef\xbb\xbf"), "bom")

    def test_offset(self) -> None:
        issue = scan.validate(b"ab\xff")
        assert issue is not None
        self.assertEqual(issue.pos, 2)


class Count(unittest.TestCase):
    def test_ascii(self) -> None:
        self.assertEqual(scan.count(b"abc"), 3)

    def test_mixed(self) -> None:
        self.assertEqual(scan.count("中文ab\U0001F600".encode()), 5)

    def test_empty(self) -> None:
        self.assertEqual(scan.count(b""), 0)

    def test_bom_flag(self) -> None:
        self.assertTrue(scan.has_bom(b"\xef\xbb\xbfa"))
        self.assertFalse(scan.has_bom(b"a"))


if __name__ == "__main__":
    unittest.main()
