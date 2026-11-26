# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# Agent 命令行测试
#
# 覆盖完整生命周期、配置覆盖、日志组权限与三类失败出口码：参数错 2、
# 状态不允许 3、权限拒绝 4。migrate/upgrade 必须固定返回「本版单节点
# 不提供」，不得伪造成功。

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))
sys.path.insert(0, str(ROOT / "services"))

from agent.cli import main  # noqa: E402


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "agents"
        self.logs = self.base / "logs"
        self.arc = self.base / "log.archive"
        self.conf = self.base / "agents.conf"
        self.env = {
            "AIRY_AGENT_ROOT": str(self.root),
            "AIRY_AGENT_LOG_ROOT": str(self.logs),
            "AIRY_AGENT_LOG_ARCHIVE": str(self.arc),
            "AIRY_AGENT_CONF": str(self.conf),
        }
        self.patch = patch.dict(os.environ, self.env)
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.tmp.cleanup()

    def cli(self, *argv: str, groups: str = "airy-log"):
        args = ["--root", str(self.root), "--log-root", str(self.logs),
                "--log-archive", str(self.arc), "--conf", str(self.conf),
                *argv]
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"AIRY_AGENT_GROUPS": groups}):
            with redirect_stdout(out), redirect_stderr(err):
                code = main(args)
        return code, out.getvalue(), err.getvalue()

    def create(self, *extra: str):
        return self.cli("create", "--name", "bot", "--template", "chat",
                        "--token-budget", "1000", *extra)

    def test_lifecycle(self) -> None:
        code, out, _ = self.create(
            "--memory-l1", "64MB", "--memory-l2", "256MB",
            "--sched-runtime", "5ms", "--sched-deadline", "10ms",
            "--sched-period", "20ms", "--capabilities", "llm,http")
        self.assertEqual(code, 0)
        self.assertIn("1 bot INACTIVE", out)
        self.assertTrue((self.root / "1" / "meta.json").is_file())

        code, out, _ = self.cli("status", "1", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["state"], "INACTIVE")
        self.assertEqual(self.cli("start", "1")[0], 0)
        self.assertIn("bot", self.cli("list")[1])
        self.assertEqual(self.cli("pause", "1")[0], 0)
        self.assertEqual(self.cli("resume", "1")[0], 0)
        self.assertEqual(self.cli("restart", "1")[0], 0)
        self.assertEqual(self.cli("tune", "1", "--auto")[0], 0)
        self.assertEqual(self.cli("token", "1", "budget", "2000")[0], 0)
        code, out, _ = self.cli("token", "1", "status", "--json")
        self.assertEqual(json.loads(out)["total_budget"], 2000)
        self.assertEqual(self.cli("token", "1", "reset")[0], 0)
        self.assertEqual(self.cli("stop", "1")[0], 0)
        code, out, _ = self.cli("terminate", "1")
        self.assertEqual(code, 0)
        self.assertIn("DEAD", out)
        self.assertFalse((self.root / "1").exists())
        self.assertEqual(len(list((self.base / "agents.archive").iterdir())), 1)
        code, out, _ = self.create()
        self.assertEqual(code, 0)
        self.assertIn("2 bot", out)

    def test_config_override(self) -> None:
        self.conf.write_text("airy_agent.default_token_budget: 42\n",
                             encoding="utf-8")
        self.assertEqual(self.cli("create", "--name", "bot",
                                  "--template", "chat")[0], 0)
        code, out, _ = self.cli("token", "1", "status", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["total_budget"], 42)

    def test_failure_exit_codes(self) -> None:
        self.assertEqual(self.cli(
            "create", "--name", "x", "--template", "t",
            "--token-budget", "0")[0], 2)
        self.assertEqual(self.cli(
            "create", "--name", "x", "--template", "t",
            "--memory-l1", "0MB")[0], 2)
        self.assertEqual(self.cli("status", "abc")[0], 2)
        self.assertEqual(self.create()[0], 0)
        self.assertEqual(self.cli("start", "1")[0], 0)
        self.assertEqual(self.cli("start", "1")[0], 3)
        self.assertEqual(self.cli("list", "--state", "NOPE")[0], 2)
        self.assertEqual(self.cli("status", "9")[0], 3)
        self.assertEqual(self.cli("tune", "1", "--auto",
                                  "--sched-runtime", "10ms")[0], 2)
        self.assertEqual(self.create()[0], 0)
        self.assertEqual(self.cli("stop", "2")[0], 3)

    def test_log_permission_and_tail(self) -> None:
        self.assertEqual(self.create()[0], 0)
        folder = self.logs / "1"
        folder.mkdir(parents=True)
        (folder / "today.log").write_text("a\nb\n", encoding="utf-8")
        self.assertEqual(self.cli("log", "1", groups="users")[0], 4)
        code, out, _ = self.cli("log", "1", "--lines", "1")
        self.assertEqual((code, out.strip()), (0, "b"))
        self.assertEqual(self.cli("log", "bad")[0], 2)
        self.assertEqual(self.cli("log", "archive", "1")[0], 0)

    def test_unsupported_scope(self) -> None:
        code, _, err = self.cli("migrate", "1", "--target-node", "node2")
        self.assertEqual(code, 3)
        self.assertIn("本版单节点不提供", err)
        code, _, err = self.cli("upgrade", "--group", "g", "--version", "1")
        self.assertEqual(code, 3)
        self.assertIn("本版单节点不提供", err)


if __name__ == "__main__":
    unittest.main()
