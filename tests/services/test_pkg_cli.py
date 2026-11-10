# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# airy-pkg 单测
#
# 覆盖后缀分派、转发参数、rpm 回显与确认、取消无副作用、后缀与选项的
# 用法错误，以及非 root 时先提权再碰文件。

"""Tests for the airy-pkg command line."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airypkg import cli  # noqa: E402


def run(argv):
    out = io.StringIO()
    errout = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(errout):
        code = cli.main(argv)
    return code, out.getvalue(), errout.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="airy-pkg-")
        self.base = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def file(self, name):
        path = self.base / name
        path.write_bytes(b"pkg")
        return path

    def done(self, code=0, stdout=""):
        return mock.Mock(returncode=code, stdout=stdout)


class DebTest(Base):
    def test_forwards_ask(self):
        deb = self.file("demo.deb")
        with mock.patch.object(cli.os, "geteuid", return_value=0), \
                mock.patch.object(cli.shutil, "which",
                                  return_value="/usr/bin/airy-deb"), \
                mock.patch.object(cli.subprocess, "run",
                                  return_value=self.done()) as spawn:
            code, _, _ = run(["install", str(deb), "--ask"])
        self.assertEqual(code, 0)
        self.assertEqual(spawn.call_args.args[0],
                         ["/usr/bin/airy-deb", "install", str(deb), "--ask"])

    def test_forwards_ignore_scripts(self):
        deb = self.file("demo.deb")
        with mock.patch.object(cli.os, "geteuid", return_value=0), \
                mock.patch.object(cli.shutil, "which",
                                  return_value="/usr/bin/airy-deb"), \
                mock.patch.object(cli.subprocess, "run",
                                  return_value=self.done()) as spawn:
            code, _, _ = run(["install", str(deb), "--ignore-scripts"])
        self.assertEqual(code, 0)
        self.assertIn("--ignore-scripts", spawn.call_args.args[0])
        self.assertNotIn("--ask", spawn.call_args.args[0])

    def test_propagates_exit_code(self):
        deb = self.file("demo.deb")
        with mock.patch.object(cli.os, "geteuid", return_value=0), \
                mock.patch.object(cli.shutil, "which",
                                  return_value="/usr/bin/airy-deb"), \
                mock.patch.object(cli.subprocess, "run",
                                  return_value=self.done(code=5)):
            code, _, _ = run(["install", str(deb)])
        self.assertEqual(code, 5)

    def test_missing_helper(self):
        deb = self.file("demo.deb")
        with mock.patch.object(cli.os, "geteuid", return_value=0), \
                mock.patch.object(cli.shutil, "which", return_value=None):
            code, _, errout = run(["install", str(deb)])
        self.assertEqual(code, 4)
        self.assertIn("airy-deb", errout)


class RpmTest(Base):
    def install(self, argv, meta="demo 1.0-1 x86_64\n", answer=True):
        rpm = self.file("demo.rpm")
        calls = [self.done(stdout=meta), self.done(), self.done()]
        with mock.patch.object(cli.os, "geteuid", return_value=0), \
                mock.patch.object(cli.shutil, "which",
                                  return_value="/usr/bin/tool"), \
                mock.patch.object(cli.subprocess, "run",
                                  side_effect=calls) as spawn, \
                mock.patch.object(cli, "ask", return_value=answer) as asked:
            code, out, errout = run(["install", str(rpm), *argv])
        return code, out, errout, spawn, asked

    def test_ask_echoes_and_installs(self):
        code, out, _, spawn, asked = self.install(["--ask"])
        self.assertEqual(code, 0)
        self.assertIn("package: demo 1.0-1 (x86_64)", out)
        self.assertIn("+ dnf install -y", out)
        asked.assert_called_once()
        self.assertEqual(spawn.call_args.args[0][:2], ["dnf", "install"])

    def test_cancel_leaves_system_alone(self):
        code, _, errout, spawn, _ = self.install(["--ask"], answer=False)
        self.assertEqual(code, 5)
        self.assertIn("已取消", errout)
        self.assertEqual(spawn.call_count, 1)

    def test_plain_install_does_not_prompt(self):
        code, _, _, spawn, asked = self.install([])
        self.assertEqual(code, 0)
        asked.assert_not_called()
        self.assertEqual(spawn.call_count, 2)

    def test_failed_install(self):
        rpm = self.file("demo.rpm")
        calls = [self.done(stdout="demo 1.0-1 x86_64\n"), self.done(code=1)]
        with mock.patch.object(cli.os, "geteuid", return_value=0), \
                mock.patch.object(cli.shutil, "which",
                                  return_value="/usr/bin/tool"), \
                mock.patch.object(cli.subprocess, "run",
                                  side_effect=calls):
            code, _, errout = run(["install", str(rpm)])
        self.assertEqual(code, 6)
        self.assertIn("dnf", errout)

    def test_unreadable_rpm(self):
        code, _, errout, _, _ = self.install([],
                                             meta="")
        self.assertEqual(code, 4)
        self.assertIn("不是可读的 RPM", errout)

    def test_rejects_ignore_scripts(self):
        rpm = self.file("demo.rpm")
        code, _, errout = run(["install", str(rpm), "--ignore-scripts"])
        self.assertEqual(code, 2)
        self.assertIn("--ignore-scripts", errout)


class GuardTest(Base):
    def test_unknown_suffix(self):
        path = self.file("demo.txt")
        code, _, errout = run(["install", str(path)])
        self.assertEqual(code, 2)
        self.assertIn("不认识的后缀", errout)

    def test_missing_file(self):
        code, _, errout = run(["install", str(self.base / "nope.deb")])
        self.assertEqual(code, 4)
        self.assertIn("不是可读文件", errout)

    def test_elevates_before_reading(self):
        deb = self.file("demo.deb")
        with mock.patch.object(cli.os, "geteuid", return_value=1000), \
                mock.patch.object(cli, "elevate",
                                  return_value=0) as lift, \
                mock.patch.object(cli.subprocess, "run") as spawn:
            code, _, _ = run(["install", str(deb), "--ask"])
        self.assertEqual(code, 0)
        self.assertEqual(lift.call_args.args[0], "airy-pkg")
        self.assertEqual(lift.call_args.args[1],
                         ["install", str(deb.resolve()), "--ask"])
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
