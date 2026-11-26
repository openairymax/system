# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 记忆命令行测试
#
# 覆盖 status/stats/list/search/defrag/transfer/clone/erase 的输出与退出码，
# 以及 --json 事实字段、配置覆盖与环境变量根。

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))
sys.path.insert(0, str(ROOT / "services"))

from memory.cli import main  # noqa: E402
from memory.model import Item  # noqa: E402
from memory.store import Store  # noqa: E402


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.mem = self.base / "mem"
        self.logs = self.base / "logs"
        self.env = mock.patch.dict(os.environ, {
            "AIRY_MEMORY_LOGDIR": str(self.logs),
            "AIRY_MEMORY_OPERATOR": "cli-user",
        })
        self.env.start()
        self.store = Store(str(self.mem), 4)
        self.store.save_items("L3", "7", [
            Item(f"item{n}", b"v" * 40) for n in range(1, 6)])
        self.store.save_items("L3", "8", [
            Item(f"other{n}", b"w" * 40) for n in range(1, 4)],
            owner="team")

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv: str) -> tuple:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--root", str(self.mem),
                         "--conf", str(self.base / "none.conf"), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_status_json_reports_facts(self) -> None:
        code, out, _ = self.run_cli("status", "7", "--json")
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual(doc["agent"], "7")
        self.assertEqual(doc["levels"]["L3"]["items"], 5)
        self.assertEqual(doc["levels"]["L3"]["checksum"], "ok")
        self.assertNotIn("hit_rate_pct", out)

    def test_status_missing_agent_exit_3(self) -> None:
        code, _, err = self.run_cli("status", "99")
        self.assertEqual(code, 3)
        self.assertIn("不存在", err)

    def test_stats_text_and_json(self) -> None:
        code, out, _ = self.run_cli("stats", "7")
        self.assertEqual(code, 0)
        self.assertIn("items=5", out)
        self.assertIn("archive_bytes=0", out)
        code, out, _ = self.run_cli("stats", "7", "--json")
        doc = json.loads(out)
        self.assertEqual(doc["levels"]["L3"]["dead_items"], 0)

    def test_list_and_search(self) -> None:
        code, out, _ = self.run_cli("list", "7", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["items"]), 5)
        code, out, _ = self.run_cli("search", "7", "item3")
        self.assertEqual(code, 0)
        self.assertIn("item3", out)
        code, out, _ = self.run_cli("search", "7", "absent")
        self.assertEqual((code, out.strip()), (0, ""))

    def test_list_missing_exit_3(self) -> None:
        code, _, _ = self.run_cli("list", "77")
        self.assertEqual(code, 3)

    def test_defrag_all_and_status(self) -> None:
        self.store.remove_items("L3", "7", ["item1", "item2", "item3"])
        code, out, _ = self.run_cli("defrag", "--all", "--now")
        self.assertEqual(code, 0)
        self.assertIn("state=done", out)
        code, out, _ = self.run_cli("defrag", "status", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["state"], "done")

    def test_defrag_bad_target_exit_2(self) -> None:
        code, _, _ = self.run_cli("defrag", "7", "--all")
        self.assertEqual(code, 2)
        code, _, _ = self.run_cli("defrag", "99")
        self.assertEqual(code, 3)

    def test_clone_and_transfer(self) -> None:
        code, out, _ = self.run_cli("clone", "--from-agent", "7",
                                    "--to-agent", "9",
                                    "--items", "item1,item2")
        self.assertEqual(code, 0)
        self.assertIn("clone from=7 to=9", out)
        self.assertEqual(len(self.store.items("L3", "9")), 2)
        code, out, _ = self.run_cli("transfer", "--from-agent", "7",
                                    "--to-agent", "8",
                                    "--items", "item4", "--consent")
        self.assertEqual(code, 0)
        self.assertIn("move from=7 to=8", out)

    def test_transfer_cross_owner_exit_4(self) -> None:
        code, _, err = self.run_cli("transfer", "--from-agent", "7",
                                    "--to-agent", "8", "--items", "item4")
        self.assertEqual(code, 4)
        self.assertIn("consent", err)

    def test_erase_and_audit(self) -> None:
        code, out, _ = self.run_cli("erase", "7", "--all", "--method", "secure")
        self.assertEqual(code, 0)
        self.assertIn("method=secure", out)
        self.assertIn("operator=cli-user", out)
        code, out, _ = self.run_cli("erase", "audit", "--json")
        self.assertEqual(code, 0)
        rows = json.loads(out)
        self.assertEqual(rows[0]["passes"], 3)
        code, out, _ = self.run_cli("erase", "status", "--json")
        self.assertEqual(json.loads(out)["method"], "secure")

    def test_erase_level_as_method(self) -> None:
        code, out, _ = self.run_cli("erase", "7", "--all", "--level", "secure")
        self.assertEqual(code, 0)
        self.assertIn("method=secure", out)
        self.assertIn("level=L3", out)

    def test_crypto_exit_3(self) -> None:
        code, _, err = self.run_cli("erase", "7", "--all",
                                    "--level", "L4", "--method", "crypto")
        self.assertEqual(code, 3)
        self.assertIn("本版不提供", err)
        self.assertTrue(self.store.exists("L3", "7"))

    def test_bad_input_exit_2(self) -> None:
        self.assertEqual(self.run_cli("erase")[0], 2)
        self.assertEqual(self.run_cli("status", "0")[0], 2)
        self.assertEqual(self.run_cli("search", "7", " ")[0], 2)
        self.assertEqual(self.run_cli("erase", "7", "--items", "item1",
                                      "--all")[0], 2)

    def test_config_override_block_kb(self) -> None:
        conf = self.base / "memory.conf"
        conf.write_text("airy_memory.l3_block_kb = 1\n", encoding="utf-8")
        small = Store(str(self.base / "small"), 1)
        small.save_items("L3", "1", [Item(f"x{n}", b"y" * 200)
                                     for n in range(1, 9)])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--root", str(self.base / "small"),
                         "--conf", str(conf), "status", "1", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["levels"]["L3"]["block_kb"],
                         1)
        conf.write_text("airy_memory.defrag_window = 99\n", encoding="utf-8")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = main(["--root", str(self.base / "small"),
                         "--conf", str(conf), "status", "1"])
        self.assertEqual(code, 3)

    def test_env_root_override(self) -> None:
        with mock.patch.dict(os.environ, {
                "AIRY_MEMORY_ROOT": str(self.mem),
                "AIRY_MEMORY_CONF": str(self.base / "none.conf")}):
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = main(["status", "7", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["agent"], "7")


if __name__ == "__main__":
    unittest.main()
