# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 升级命令行测试
#
# check 的三条退出路径与状态保留必须各自可验证：有新版、无新版、失败。
# 失败时保留 last_ok 是本功能唯一的「记忆」，测试要盯住它而不是只盯
# 退出码。

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyupdate import cli, local, model, net, state  # noqa: E402

STAMP = "2026-11-02T00:00:00Z"


def identity(version: str = "0.0.3", build: int = 19) -> model.Identity:
    return model.Identity(version, build, "6.6.0-1.airy")


def manifest(version: str = "0.0.4", build: int = 3) -> model.Manifest:
    return model.Manifest(
        channel="stable",
        version=version,
        build=build,
        released_at="2026-11-20T00:00:00Z",
        iso=model.Asset("airymaxos-0.0.4.iso", 1024, "a" * 64),
        kernel=model.Kernel("airymaxos-kernel-0.0.4-3.x86_64", "b" * 64),
        notes_url="https://github.com/openairymax/agent-linux/releases/tag/v0.0.4",
    )


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.path = state.state_path(self.home)
        self.args = SimpleNamespace(state_dir=self.home, os_release=Path("/x"))
        self.out = io.StringIO()
        self.err = io.StringIO()

    def invoke(self, here=None, remote=None, error=None) -> int:
        patches = [
            mock.patch.object(cli, "_now", return_value=STAMP),
            mock.patch.object(cli.local, "local_identity",
                              return_value=here or identity()),
        ]
        if error is None:
            patches.append(mock.patch.object(cli.net, "fetch_manifest",
                                             return_value=remote or manifest()))
        else:
            patches.append(mock.patch.object(cli.net, "fetch_manifest",
                                             side_effect=error))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with redirect_stdout(self.out), redirect_stderr(self.err):
            return cli._check(self.args)


class CheckTest(Base):
    def test_new_release(self) -> None:
        self.assertEqual(self.invoke(), model.EXIT_NEW)
        self.assertIn("new release", self.out.getvalue())
        saved = state.load(self.path)
        self.assertEqual(saved.last_ok.version, "0.0.4")
        self.assertEqual(saved.last_ok.build, 3)
        self.assertEqual(saved.last_ok.checked_at, STAMP)

    def test_same(self) -> None:
        code = self.invoke(here=identity("0.0.4", 3))
        self.assertEqual(code, model.EXIT_OK)
        self.assertIn("up to date", self.out.getvalue())

    def test_rebuild_is_not_newer(self) -> None:
        code = self.invoke(here=identity("0.0.3", 19),
                           remote=manifest("0.0.3", 22))
        self.assertEqual(code, model.EXIT_OK)
        self.assertIn("same version", self.out.getvalue())
        self.assertEqual(state.load(self.path).last_ok.build, 22)

    def test_local_ahead(self) -> None:
        code = self.invoke(here=identity("0.0.4", 3),
                           remote=manifest("0.0.3", 22))
        self.assertEqual(code, model.EXIT_OK)
        self.assertIn("ahead", self.out.getvalue())

    def test_failure_keeps_last_ok(self) -> None:
        state.save(self.path, state.State(last_ok=state.Finding(
            "0.0.3", 19, STAMP)))
        code = self.invoke(error=net.Fetch("offline"))
        self.assertEqual(code, model.EXIT_FAIL)
        saved = state.load(self.path)
        self.assertEqual(saved.last_ok.version, "0.0.3")
        self.assertEqual(saved.last_error.reason, "offline")

    def test_failure_reason_is_one_line(self) -> None:
        self.invoke(error=net.Fetch("a\nb\tc"))
        self.assertEqual(state.load(self.path).last_error.reason, "a b c")

    def test_probe_failure(self) -> None:
        with mock.patch.object(cli.local, "local_identity",
                               side_effect=local.Probe("no os-release")), \
                mock.patch.object(cli, "_now", return_value=STAMP), \
                redirect_stderr(self.err):
            code = cli._check(self.args)
        self.assertEqual(code, model.EXIT_FAIL)
        self.assertIn("no os-release", self.err.getvalue())
        self.assertNotIn("Traceback", self.err.getvalue())

    def test_state_unwritable(self) -> None:
        with mock.patch.object(cli.state, "save", side_effect=PermissionError):
            self.assertEqual(self.invoke(), model.EXIT_NEW)
        self.assertIn("state", self.err.getvalue())

    def test_corrupt_state_heals(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("not json", encoding="utf-8")
        self.assertEqual(self.invoke(), model.EXIT_NEW)
        self.assertIn("state", self.err.getvalue())
        self.assertEqual(state.load(self.path).last_ok.version, "0.0.4")


class StatusTest(Base):
    def status(self, here=None, error=None) -> int:
        patches = [mock.patch.object(cli.local, "local_identity",
                                     return_value=here or identity())]
        if error is not None:
            patches[-1] = mock.patch.object(cli.local, "local_identity",
                                            side_effect=error)
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with redirect_stdout(self.out), redirect_stderr(self.err):
            return cli._status(self.args)

    def test_no_state(self) -> None:
        self.assertEqual(self.status(), model.EXIT_FAIL)
        self.assertIn("version: 0.0.3", self.out.getvalue())
        self.assertIn("last_ok: none", self.out.getvalue())

    def test_with_state(self) -> None:
        state.save(self.path, state.State(
            last_ok=state.Finding("0.0.4", 3, STAMP),
            last_error=state.Failure("offline", STAMP),
            notified="0.0.4/3"))
        self.assertEqual(self.status(), model.EXIT_OK)
        text = self.out.getvalue()
        self.assertIn("last_ok: 0.0.4 build 3", text)
        self.assertIn("last_error: offline", text)
        self.assertIn("notified: 0.0.4/3", text)

    def test_corrupt(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{", encoding="utf-8")
        self.assertEqual(self.status(), model.EXIT_FAIL)
        self.assertIn("state", self.err.getvalue())

    def test_probe_failure(self) -> None:
        self.assertEqual(self.status(error=local.Probe("boom")),
                         model.EXIT_FAIL)
        self.assertIn("boom", self.err.getvalue())


class NotifyTest(Base):
    def run_notify(self, here=None, error=None, sent=True) -> int:
        patches = [mock.patch.object(cli, "_send", return_value=sent)]
        if error is None:
            patches.append(mock.patch.object(
                cli.local, "local_identity", return_value=here or identity()))
        else:
            patches.append(mock.patch.object(
                cli.local, "local_identity", side_effect=error))
        for patch in patches:
            started = patch.start()
            self.addCleanup(patch.stop)
            if patch is patches[0]:
                self.send = started
        with redirect_stdout(self.out), redirect_stderr(self.err):
            return cli._notify(self.args)

    def record(self, version: str = "0.0.4", build: int = 3,
               notified: str | None = None) -> None:
        state.save(self.path, state.State(
            last_ok=state.Finding(version, build, STAMP), notified=notified))

    def test_no_state(self) -> None:
        self.assertEqual(self.run_notify(), model.EXIT_OK)
        self.send.assert_not_called()

    def test_newer_sends_once(self) -> None:
        self.record()
        self.assertEqual(self.run_notify(), model.EXIT_OK)
        self.send.assert_called_once()
        self.assertEqual(state.load(self.path).notified, "0.0.4/3")

    def test_same_skips(self) -> None:
        self.record()
        self.assertEqual(self.run_notify(here=identity("0.0.4", 3)),
                         model.EXIT_OK)
        self.send.assert_not_called()

    def test_rebuild_skips(self) -> None:
        self.record("0.0.3", 22)
        self.assertEqual(self.run_notify(), model.EXIT_OK)
        self.send.assert_not_called()

    def test_repeat_skips(self) -> None:
        self.record(notified="0.0.4/3")
        self.assertEqual(self.run_notify(), model.EXIT_OK)
        self.send.assert_not_called()

    def test_other_key_sends(self) -> None:
        self.record(notified="0.0.3/22")
        self.assertEqual(self.run_notify(), model.EXIT_OK)
        self.send.assert_called_once()
        self.assertEqual(state.load(self.path).notified, "0.0.4/3")

    def test_send_failure_keeps(self) -> None:
        self.record()
        self.assertEqual(self.run_notify(sent=False), model.EXIT_OK)
        self.assertIsNone(state.load(self.path).notified)

    def test_corrupt_state(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{", encoding="utf-8")
        self.assertEqual(self.run_notify(), model.EXIT_OK)
        self.assertIn("state", self.err.getvalue())

    def test_probe_failure(self) -> None:
        self.record()
        self.assertEqual(self.run_notify(error=local.Probe("boom")),
                         model.EXIT_OK)
        self.send.assert_not_called()
        self.assertIn("boom", self.err.getvalue())


class SendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.err = io.StringIO()
        self.found = state.Finding("0.0.4", 3, STAMP)
        self.patch = mock.patch.object(cli.subprocess, "run")
        self.run = self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_success(self) -> None:
        self.run.return_value = SimpleNamespace(returncode=0, stderr="")
        with redirect_stderr(self.err):
            self.assertTrue(cli._send(self.found))
        argv = self.run.call_args.args[0]
        self.assertEqual(argv[0], "notify-send")
        self.assertIn("0.0.4", argv[-2])

    def test_nonzero(self) -> None:
        self.run.return_value = SimpleNamespace(returncode=1,
                                                stderr="no session\n")
        with redirect_stderr(self.err):
            self.assertFalse(cli._send(self.found))
        self.assertIn("no session", self.err.getvalue())

    def test_missing_binary(self) -> None:
        self.run.side_effect = FileNotFoundError("notify-send")
        with redirect_stderr(self.err):
            self.assertFalse(cli._send(self.found))
        self.assertIn("notify", self.err.getvalue())

    def test_timeout(self) -> None:
        self.run.side_effect = subprocess.TimeoutExpired("notify-send", 15)
        with redirect_stderr(self.err):
            self.assertFalse(cli._send(self.found))


class ArgsTest(unittest.TestCase):
    def test_requires_command(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli._args([])

    def test_defaults(self) -> None:
        args = cli._args(["check"])
        self.assertEqual(args.state_dir, cli.STATE_HOME)
        self.assertIs(args.func, cli._check)

    def test_notify(self) -> None:
        self.assertIs(cli._args(["notify"]).func, cli._notify)


if __name__ == "__main__":
    unittest.main()
