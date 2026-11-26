# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""命令行测试：退出码、审计留痕与显式拒绝策略。"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import station_support as support

from devstation import audit, cli


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        support.make(self.root)
        self.logdir = self.root / "logs"
        self.conf = self.root / "none.conf"
        self.ident = "1"
        self.env = mock.patch.dict(os.environ, {
            audit.ENV: str(self.logdir),
            audit.OPERATOR: "gate",
            "AIRY_MEMORY_LOGDIR": str(self.logdir),
        }, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *extra, conf=None):
        argv = ["--root", str(self.root), "--conf", str(conf or self.conf),
                "--log-dir", str(self.logdir)] + list(extra)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def audits(self):
        return audit.read()


class ViewTest(Base):
    def test_snapshot_json(self):
        code, out, _ = self.run_cli("snapshot", "--json")
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual(len(doc["sections"]), 8)
        self.assertEqual(doc["sections"]["monitor"]["status"], "ok")
        self.assertEqual(doc["sections"]["agents"]["rows"][0]["name"],
                         "alpha")

    def test_snapshot_text(self):
        code, out, _ = self.run_cli("snapshot")
        self.assertEqual(code, 0)
        self.assertIn("monitor rows=1", out)
        self.assertIn("level=view", out)

    def test_monitor_once(self):
        code, out, _ = self.run_cli("monitor", "--once")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), 30)
        self.assertTrue(all(len(line) <= 100 for line in lines))
        self.assertIn("airymaxmon", out)

    def test_each_panel(self):
        for name, needle in (("logs", "airy-agent"), ("alerts", "A-1"),
                             ("incidents", "daemon down"),
                             ("memory", "l3_items"), ("backup", "FULL-"),
                             ("config", "agent.max_instances")):
            with self.subTest(panel=name):
                code, out, _ = self.run_cli(name, "--json")
                self.assertEqual(code, 0)
                doc = json.loads(out)
                self.assertEqual(doc["panel"], name)
                self.assertEqual(doc["status"], "ok")
                self.assertIn(needle, json.dumps(doc, ensure_ascii=False))

    def test_logs_lines_override(self):
        code, out, _ = self.run_cli("logs", "--lines", "1", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["rows"]), 1)

    def test_missing_root_degrades(self):
        empty = self.root / "empty"
        empty.mkdir()
        argv = ["--root", str(empty), "--conf", str(self.conf),
                "--log-dir", str(self.logdir), "snapshot", "--json"]
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(argv)
        self.assertEqual(code, 0)
        doc = json.loads(out.getvalue())
        self.assertTrue(all(sec["status"] == "unavailable"
                            for sec in doc["sections"].values()))


class RejectTest(Base):
    def test_web_rejected(self):
        for extra in (("--web", "snapshot"), ("monitor", "--web")):
            with self.subTest(extra=extra):
                code, _, err = self.run_cli(*extra)
                self.assertEqual(code, 3)
                self.assertIn("v1.1.0", err)

    def test_node_and_rpc_rejected(self):
        code, _, err = self.run_cli("--node", "n2", "snapshot")
        self.assertEqual(code, 3)
        self.assertIn("--node", err)
        code, _, err = self.run_cli("--rpc-port", "9000", "snapshot")
        self.assertEqual(code, 3)
        self.assertIn("--rpc-port", err)

    def test_web_enabled_config_rejected(self):
        conf = self.root / "web.conf"
        conf.write_text("devstation.web_enabled = true\n", encoding="utf-8")
        code, _, err = self.run_cli("snapshot", conf=conf)
        self.assertEqual(code, 3)
        self.assertIn("v1.1.0", err)

    def test_bad_config(self):
        conf = self.root / "bad.conf"
        conf.write_text("devstation.refresh_interval_s = 0\n", encoding="utf-8")
        code, _, err = self.run_cli("snapshot", conf=conf)
        self.assertEqual(code, 3)
        self.assertIn("refresh_interval_s", err)

    def test_bad_level(self):
        code, _, err = self.run_cli("--level", "root", "snapshot")
        self.assertEqual(code, 2)
        self.assertTrue(err)


class PermTest(Base):
    def test_view_operate_denied(self):
        code, _, err = self.run_cli("agents", "--id", self.ident,
                                    "--action", "start")
        self.assertEqual(code, 4)
        self.assertIn("operate", err)
        self.assertEqual(self.audits()[-1]["result"], "denied")

    def test_operate_start(self):
        code, out, _ = self.run_cli("--level", "operate", "agents",
                                    "--id", self.ident, "--action", "start")
        self.assertEqual(code, 0)
        self.assertIn("RUNNING", out)
        self.assertEqual(self.audits()[-1]["result"], "ok")

    def test_admin_needs_yes(self):
        code, _, err = self.run_cli("--level", "admin", "memory",
                                    "--action", "erase", "--agent", self.ident,
                                    "--items", "keep")
        self.assertEqual(code, 4)
        self.assertIn("--yes", err)
        self.assertEqual(self.audits()[-1]["result"], "denied")

    def test_admin_erase(self):
        code, out, _ = self.run_cli("--level", "admin", "--yes", "memory",
                                    "--action", "erase", "--agent", self.ident,
                                    "--items", "keep")
        self.assertEqual(code, 0)
        self.assertIn("passes=", out)
        rows = self.audits()
        self.assertEqual(rows[-1]["action"], "memory.erase")
        self.assertEqual(rows[-1]["result"], "ok")


if __name__ == "__main__":
    unittest.main()
