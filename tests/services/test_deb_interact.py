# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# interact 单测
#
# 覆盖确认语的两个方向（显式肯定继续，其余一律取消）、无终端拒绝，以及
# 提权的三条出口（缺 pkexec、缺入口、授权被拒）与退出码透传。判据只认
# 返回码与文案，不模拟 polkit。

"""Tests for the shared install interaction."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airydeb import err, interact  # noqa: E402


def ask_with(reply):
    tty = mock.Mock()
    tty.isatty.return_value = True
    with mock.patch.object(interact.sys, "stdin", tty), \
            mock.patch("builtins.input", side_effect=[reply]):
        return interact.ask("ok? ")


class AskTest(unittest.TestCase):
    def test_explicit_yes_accepted(self):
        for reply in ("y", "YES", " Yes "):
            self.assertTrue(ask_with(reply), reply)

    def test_other_input_cancels(self):
        for reply in ("n", "", "yeah", "1"):
            self.assertFalse(ask_with(reply), reply)

    def test_eof_cancels(self):
        tty = mock.Mock()
        tty.isatty.return_value = True
        with mock.patch.object(interact.sys, "stdin", tty), \
                mock.patch("builtins.input", side_effect=EOFError):
            self.assertFalse(interact.ask("ok? "))

    def test_no_tty_refused(self):
        tty = mock.Mock()
        tty.isatty.return_value = False
        with mock.patch.object(interact.sys, "stdin", tty):
            with self.assertRaises(err.Fault) as caught:
                interact.ask("ok? ")
        self.assertEqual(caught.exception.code, err.EXIT_POLICY)
        self.assertIn("没有终端", str(caught.exception))


class ElevateTest(unittest.TestCase):
    def test_missing_pkexec(self):
        with mock.patch.object(interact.shutil, "which", return_value=None):
            with self.assertRaises(err.Fault) as caught:
                interact.elevate("airy-deb", ["install", "x.deb"])
        self.assertEqual(caught.exception.code, err.EXIT_ENV)
        self.assertIn("sudo airy-deb", str(caught.exception))

    def test_missing_entry(self):
        def which(name):
            return "/usr/bin/pkexec" if name == "pkexec" else None

        with mock.patch.object(interact.shutil, "which", side_effect=which):
            with self.assertRaises(err.Fault) as caught:
                interact.elevate("airy-deb", [])
        self.assertEqual(caught.exception.code, err.EXIT_ENV)
        self.assertIn("airy-deb", str(caught.exception))

    def test_denied_maps_to_policy(self):
        with mock.patch.object(interact.shutil, "which",
                               return_value="/usr/bin/pkexec"), \
                mock.patch.object(interact.subprocess, "run",
                                  return_value=mock.Mock(returncode=126)):
            out = io.StringIO()
            with contextlib.redirect_stderr(out):
                code = interact.elevate("airy-deb", ["install", "x.deb"])
        self.assertEqual(code, err.EXIT_POLICY)
        self.assertIn("126", out.getvalue())

    def test_exit_passthrough(self):
        with mock.patch.object(interact.shutil, "which",
                               return_value="/usr/bin/pkexec"), \
                mock.patch.object(interact.subprocess, "run",
                                  return_value=mock.Mock(returncode=6)):
            self.assertEqual(interact.elevate("airy-deb", []), 6)


if __name__ == "__main__":
    unittest.main()
