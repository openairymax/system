# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# cli 单测
#
# 覆盖退出码语义、维护者脚本的拒绝与放行、--dry-run 只打印不构建、非 deb
# 与不认识架构走环境错误，以及 convert 把构造成品复制到 -o 目录。

"""Tests for the airy-deb command line."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from airydeb import cli, err  # noqa: E402
from deb_fixture import Tree  # noqa: E402

POSTINST = "#!/bin/sh\necho postinst\nexit 0\n"


def run(argv):
    out = io.StringIO()
    errout = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(errout):
        code = cli.main(argv)
    return code, out.getvalue(), errout.getvalue()


class InfoTest(Tree):
    def test_prints_metadata(self):
        deb = self.deb(depends="libc6 (>= 2.34)", recommends="curl",
                       scripts={"postinst": POSTINST})
        code, out, errout = run(["info", str(deb)])
        self.assertEqual(code, err.EXIT_OK)
        self.assertEqual(errout, "")
        self.assertIn("package:      demo", out)
        self.assertIn("version:      1.0-1", out)
        self.assertIn("architecture: amd64", out)
        self.assertIn("depends:      libc6 (>= 2.34)", out)
        self.assertIn("recommends:   curl", out)
        self.assertIn("scripts:      postinst", out)
        self.assertIn("members:      2", out)
        self.assertIn("bytes:        ", out)

    def test_rejects_non_deb(self):
        path = self.base / "plain.deb"
        path.write_text("not a deb\n")
        code, _, errout = run(["info", str(path)])
        self.assertEqual(code, err.EXIT_ENV)
        self.assertIn("不是 deb", errout)


class InstallTest(Tree):
    def test_blocks_maintainer_script(self):
        deb = self.deb(scripts={"postinst": POSTINST})
        with mock.patch.object(cli, "_build") as build:
            code, _, errout = run(["install", str(deb)])
        self.assertEqual(code, err.EXIT_POLICY)
        self.assertIn("postinst", errout)
        self.assertIn("echo postinst", errout)
        build.assert_not_called()

    def test_ignore_scripts_dry_run(self):
        deb = self.deb(scripts={"postinst": POSTINST})
        with mock.patch.object(cli, "_build") as build:
            code, out, errout = run(
                ["install", str(deb), "--ignore-scripts", "--dry-run"])
        self.assertEqual(code, err.EXIT_OK)
        build.assert_not_called()
        self.assertIn("+ rpmbuild", out)
        self.assertIn("+ dnf install", out)
        self.assertIn("echo postinst", errout)

    def test_dry_run_reports_depends(self):
        deb = self.deb(depends="libc6 (>= 2.34)")
        code, _, errout = run(["install", str(deb), "--dry-run"])
        self.assertEqual(code, err.EXIT_OK)
        self.assertIn("Depends: libc6 (>= 2.34)", errout)

    def test_rejects_unknown_arch(self):
        deb = self.deb(arch="i386")
        code, _, errout = run(["install", str(deb), "--dry-run"])
        self.assertEqual(code, err.EXIT_ENV)
        self.assertIn("i386", errout)

    def test_missing_tool_names_package(self):
        with mock.patch.object(cli.shutil, "which", return_value=None):
            with self.assertRaises(err.Fault) as caught:
                cli._need(*cli.RPMBUILD)
        self.assertIn("rpm-build", str(caught.exception))


class ConvertTest(Tree):
    def test_copies_rpm(self):
        deb = self.deb(scripts={"postinst": POSTINST})
        fake = self.base / "demo-1.0-1.x86_64.rpm"
        fake.write_bytes(b"rpm")
        out = self.base / "out"
        with mock.patch.object(cli, "_build", return_value=fake):
            code, stdout, errout = run(
                ["convert", str(deb), "-o", str(out)])
        self.assertEqual(code, err.EXIT_OK)
        self.assertEqual((out / fake.name).read_bytes(), b"rpm")
        self.assertIn(str(out / fake.name), stdout)
        self.assertIn("维护者脚本不会写入 RPM", errout)

    def test_rejects_non_deb(self):
        path = self.base / "plain.deb"
        path.write_text("nope\n")
        code, _, _ = run(["convert", str(path)])
        self.assertEqual(code, err.EXIT_ENV)


if __name__ == "__main__":
    unittest.main()
