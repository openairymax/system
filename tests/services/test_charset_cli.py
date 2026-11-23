# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# airy-charset 命令行测试
#
# 四条子命令都要有成功与失败两条路径：show/count 只读；check 的退出码要能
# 区分「文件缺」「序列坏」；convert 要覆盖白名单外编码、未知编码、真实转码
# 与写出失败。转换以 GB18030 为真实样例，不做桩。

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airycharset import cli  # noqa: E402

CONF = """\
CHARSET_ENC=UTF-8
CHARSET_SOURCES=GB18030,GBK
CHARSET_BOM=reject
CHARSET_NONCHAR=reject
CHARSET_COUNT=codepoint
"""


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.conf = self.root / "charset.conf"
        self.conf.write_text(CONF, encoding="utf-8")

    def file(self, name: str, data: bytes) -> str:
        path = self.root / name
        path.write_bytes(data)
        return str(path)


class Show(Base):
    def test_text(self) -> None:
        code, out, _ = run("show", "--conf", str(self.conf))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("CHARSET_ENC=UTF-8", out)
        self.assertIn("AIRYMAX-CHARSET-SHOW ok enc=UTF-8 sources=2", out)

    def test_json(self) -> None:
        code, out, _ = run("show", "--conf", str(self.conf), "--json")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(json.loads(out)["sources"], ["GB18030", "GBK"])

    def test_missing(self) -> None:
        code, _, err = run("show", "--conf", str(self.root / "none"))
        self.assertEqual(code, cli.EXIT_ENV)
        self.assertIn("缺少配置文件", err)

    def test_bad_conf(self) -> None:
        bad = self.root / "bad.conf"
        bad.write_text(CONF.replace("UTF-8", "GB18030"), encoding="utf-8")
        code, _, err = run("show", "--conf", str(bad))
        self.assertEqual(code, cli.EXIT_POLICY)
        self.assertIn("UTF-8", err)


class Check(Base):
    def test_ok(self) -> None:
        good = self.file("a.txt", "中文ab".encode())
        code, out, _ = run("check", good, "--conf", str(self.conf))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("AIRYMAX-CHARSET-CHECK ok files=1 cp=4", out)

    def test_json_ok(self) -> None:
        good = self.file("a.txt", b"abc")
        code, out, _ = run("check", good, "--conf", str(self.conf), "--json")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertTrue(json.loads(out)["ok"])

    def test_bad_bytes(self) -> None:
        bad = self.file("bad.txt", b"\xed\xa0\x80")
        code, _, err = run("check", bad, "--conf", str(self.conf))
        self.assertEqual(code, cli.EXIT_POLICY)
        self.assertIn("surrogate", err)

    def test_bom_rejected(self) -> None:
        bom = self.file("bom.txt", b"\xef\xbb\xbfab")
        code, _, err = run("check", bom, "--conf", str(self.conf))
        self.assertEqual(code, cli.EXIT_POLICY)
        self.assertIn("bom", err)

    def test_missing_file(self) -> None:
        code, _, err = run("check", str(self.root / "none"),
                           "--conf", str(self.conf))
        self.assertEqual(code, cli.EXIT_ENV)
        self.assertIn("无法读取", err)


class Count(Base):
    def test_file(self) -> None:
        good = self.file("a.txt", "中文".encode())
        code, out, _ = run("count", good, "--conf", str(self.conf))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("cp=2", out)

    def test_text(self) -> None:
        code, out, _ = run("count", "--text", "ab中", "--conf", str(self.conf))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("cp=3", out)

    def test_none(self) -> None:
        code, _, err = run("count", "--conf", str(self.conf))
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("--text", err)

    def test_bad(self) -> None:
        bad = self.file("bad.txt", b"\xff")
        code, _, err = run("count", bad, "--conf", str(self.conf))
        self.assertEqual(code, cli.EXIT_POLICY)
        self.assertIn("不是合法 UTF-8", err)


class Convert(Base):
    def test_gb18030(self) -> None:
        src = self.file("gb.txt", "极境".encode("gb18030"))
        dst = self.root / "utf.txt"
        code, _, err = run("convert", "--from", "GB18030", "--in", src,
                           "--out", str(dst), "--conf", str(self.conf))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(dst.read_bytes(), "极境".encode())
        self.assertIn("AIRYMAX-CHARSET-CONVERT ok from=GB18030", err)

    def test_not_allowed(self) -> None:
        src = self.file("a.txt", b"abc")
        code, _, err = run("convert", "--from", "KOI8-R", "--in", src,
                           "--conf", str(self.conf))
        self.assertEqual(code, cli.EXIT_POLICY)
        self.assertIn("允许入站清单", err)

    def test_unknown(self) -> None:
        src = self.file("a.txt", b"abc")
        code, _, err = run("convert", "--from", "NOPE", "--in", src,
                           "--out", os.devnull,
                           "--conf", str(self.root / "none"))
        self.assertEqual(code, cli.EXIT_POLICY)
        self.assertIn("未知编码", err)

    def test_decode_fail(self) -> None:
        src = self.file("bad.txt", b"\xff\xff")
        code, _, err = run("convert", "--from", "GB18030", "--in", src,
                           "--conf", str(self.conf))
        self.assertEqual(code, cli.EXIT_POLICY)
        self.assertIn("解码失败", err)

    def test_write_fail(self) -> None:
        src = self.file("a.txt", b"abc")
        code, _, err = run("convert", "--from", "GB18030", "--in", src,
                           "--out", str(self.root / "no/such/out"),
                           "--conf", str(self.conf))
        self.assertEqual(code, cli.EXIT_RUN)
        self.assertIn("无法写出", err)


if __name__ == "__main__":
    unittest.main()
