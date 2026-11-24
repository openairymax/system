# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 备份命令行测试
#
# 退出码是契约：参数不成立 2，仓库缺件 3，完整性失败 4。auto 的
# 全量/增量选择只由时刻决定，便于在门禁里用 --at 重放。

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))

from backup.cli import main  # noqa: E402

BASE_ISO = "2026-07-18T02:00:00Z"


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.repo = self.base / "repo"
        self.src = self.base / "src"
        (self.src / "agents" / "7").mkdir(parents=True)
        (self.src / "agents" / "7" / "state.json").write_text(
            '{"agent": 7}\n', encoding="utf-8")
        self.source = f"var/lib/airy/agents={self.src / 'agents'}"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--root", str(self.repo), "--actor", "tester",
                         *args])
        return code, out.getvalue(), err.getvalue()

    def create(self, kind: str = "incremental", at: str = BASE_ISO) -> str:
        code, out, _ = self.run_cli("--at", at, "create", "--kind", kind,
                                    "--source", self.source, "--codec", "gzip",
                                    "--json")
        self.assertEqual(code, 0)
        return json.loads(out)["backup_id"]

    def test_create_list_show(self) -> None:
        ident = self.create()
        code, out, _ = self.run_cli("list", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)[0]["backup_id"], ident)

        code, out, _ = self.run_cli("show", ident)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["kind"], "incremental")

    def test_verify_exit_codes(self) -> None:
        ident = self.create()
        code, _, _ = self.run_cli("verify", ident, "--deep")
        self.assertEqual(code, 0)

        archive = next((self.repo / "incremental").glob("*.tar.gz"))
        archive.write_bytes(b"bad")
        code, out, _ = self.run_cli("verify", ident, "--json")
        self.assertEqual(code, 4)
        self.assertFalse(json.loads(out)["ok"])

        code, _, _ = self.run_cli("verify", "FULL-20260718-999")
        self.assertEqual(code, 4)

    def test_restore_confirm_and_log(self) -> None:
        ident = self.create(kind="full")
        target = self.base / "out"
        log = self.base / "rec.log"

        code, _, err = self.run_cli("restore", ident, "--target", str(target),
                                    "--log", str(log))
        self.assertEqual(code, 2)
        self.assertIn("confirm", err)

        code, out, _ = self.run_cli("restore", ident, "--target", str(target),
                                    "--confirm", "--log", str(log), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["files"], 1)
        self.assertTrue((target / "var/lib/airy/agents/7/state.json").is_file())

    def test_bad_source_is_input_error(self) -> None:
        code, _, err = self.run_cli("create", "--kind", "full",
                                    "--source", "bogus")
        self.assertEqual(code, 2)
        self.assertIn("NAME=PATH", err)

    def test_auto_chooses_full_and_incremental(self) -> None:
        code, out, _ = self.run_cli("--at", "2026-07-18T01:30:00Z", "auto",
                                    "--source", self.source, "--codec", "gzip",
                                    "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["backup"]["kind"], "full")

        code, out, _ = self.run_cli("--at", "2026-07-18T02:30:00Z", "auto",
                                    "--source", self.source, "--codec", "gzip",
                                    "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["backup"]["kind"], "incremental")

    def test_auto_prunes_at_four(self) -> None:
        self.create("full")
        code, out, _ = self.run_cli("--at", "2026-09-01T04:10:00Z", "auto",
                                    "--source", self.source, "--codec", "gzip",
                                    "--json")
        self.assertEqual(code, 0)
        body = json.loads(out)
        self.assertIn("prune", body)
        self.assertTrue(body["prune"]["applied"])

    def test_global_options_also_accepted_after_verb(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["create", "--kind", "full", "--source", self.source,
                         "--codec", "gzip", "--root", str(self.repo),
                         "--actor", "tail", "--at", BASE_ISO, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["actor"], "tail")

    def test_stats(self) -> None:
        self.create()
        code, out, _ = self.run_cli("stats", "--json")
        self.assertEqual(code, 0)
        body = json.loads(out)
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["kinds"]["incremental"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
