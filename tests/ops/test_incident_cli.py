# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 事件台账命令行测试
#
# 命令行是运维唯一入口，出口码必须稳定：参数错 2、台账错 3、状态机拒绝 4。
# 告警取样按 alert_id 幂等，重复执行不得产生重复事件。

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))

from incident.cli import main  # noqa: E402

BASE = "2023-11-14T22:13:20Z"
LATER = "2024-03-01T00:00:00Z"


def active(rows: list) -> dict:
    return {"schema": 1, "alerts": rows}


def alert(ident: str, level: str, source: str, code: int = 7,
          message: str = "阈值命中", agent: int = 0,
          state: str = "FIRING") -> dict:
    return {
        "alert_id": ident, "severity": level, "source": source,
        "code": code, "message": message, "agent": agent, "state": state,
    }


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ledger = self.root / "ledger"
        self.alerts = self.root / "alerts"
        self.alerts.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def call(self, *args: str) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--root", str(self.ledger), *args])
        return code, buf.getvalue()

    def create(self, *extra: str) -> str:
        code, out = self.call("create", "--severity", "P1", "--category",
                             "system", "--title", "事件", "--at", BASE,
                             *extra)
        self.assertEqual(code, 0)
        return out.split()[0]

    def walk(self, ident: str) -> None:
        for step in ("triage", "respond", "recover"):
            code, _ = self.call(step, ident, "--at", BASE)
            self.assertEqual(code, 0, step)

    def test_full_path_to_postmortem(self) -> None:
        ident = self.create("--daemon", "macro_d")
        self.walk(ident)
        code, _ = self.call("resolve", ident, "--at", BASE,
                           "--root-cause", "配置错误")
        self.assertEqual(code, 0)
        code, out = self.call("postmortem", ident, "--at", BASE,
                             "--summary", "已恢复")
        self.assertEqual(code, 0)
        self.assertIn("POSTMORTEM", out)
        report = self.ledger / "postmortem" / "PM-20231114-001.md"
        self.assertTrue(report.is_file())
        body = report.read_text(encoding="utf-8")
        self.assertIn("## 4. 根因分析", body)
        self.assertIn("配置错误", body)

    def test_exit_codes(self) -> None:
        code, _ = self.call("create", "--severity", "P9", "--category",
                           "system", "--title", "事件")
        self.assertEqual(code, 2)
        code, _ = self.call("show", "INC-20231114-009")
        self.assertEqual(code, 3)
        ident = self.create()
        code, _ = self.call("resolve", ident, "--at", BASE)
        self.assertEqual(code, 4)

    def test_list_and_show_json(self) -> None:
        ident = self.create()
        code, out = self.call("list", "--json")
        self.assertEqual(code, 0)
        rows = json.loads(out)
        self.assertEqual(rows[0]["incident_id"], ident)
        code, out = self.call("show", ident, "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "DETECTED")

    def test_show_text_lists_timeline(self) -> None:
        ident = self.create()
        code, out = self.call("show", ident)
        self.assertEqual(code, 0)
        self.assertIn("时间线", out)
        self.assertIn("detected", out)

    def test_ingest_is_idempotent(self) -> None:
        rows = [alert("ALT-1", "CRITICAL", "sec_d", agent=5),
                alert("ALT-2", "WARNING", "mem_d"),
                alert("ALT-3", "INFO", "logger_d")]
        (self.alerts / "active.json").write_text(json.dumps(active(rows)),
                                                 encoding="utf-8")
        code, out = self.call("ingest", "--alerts-root", str(self.alerts),
                             "--at", BASE, "--json")
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual(len(doc["created"]), 2)
        self.assertEqual(doc["skipped"], 1)
        code, out = self.call("ingest", "--alerts-root", str(self.alerts),
                             "--at", BASE, "--json")
        self.assertEqual(json.loads(out)["created"], [])
        code, out = self.call("list", "--json")
        rows = json.loads(out)
        levels = {row["severity"] for row in rows}
        self.assertEqual(levels, {"P0", "P1"})
        first = [row for row in rows if row["alerts"] == ["ALT-1"]][0]
        self.assertEqual(first["category"], "security")
        self.assertEqual(first["affected_agents"], [5])

    def test_ingest_reports_bad_state_file(self) -> None:
        (self.alerts / "active.json").write_text("{", encoding="utf-8")
        code, _ = self.call("ingest", "--alerts-root", str(self.alerts))
        self.assertEqual(code, 3)

    def test_daemon_is_recorded(self) -> None:
        ident = self.create("--daemon", "mem_d")
        code, out = self.call("show", ident, "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["affected_daemons"], ["mem_d"])

    def test_act_is_journaled(self) -> None:
        ident = self.create()
        code, out = self.call("act", ident, "--name", "restart",
                             "--result", "failed", "--detail", "退出码 1")
        self.assertEqual(code, 0)
        self.assertIn("failed", out)
        code, out = self.call("show", ident, "--json")
        self.assertEqual(json.loads(out)["response_actions"][0]["result"],
                         "failed")

    def test_stats_and_prune(self) -> None:
        ident = self.create()
        self.walk(ident)
        self.call("resolve", ident, "--at", BASE)
        code, out = self.call("stats", "--month", "202311", "--at",
                             BASE, "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["closed"], 1)
        code, out = self.call("prune", "--days", "90", "--at", LATER,
                             "--json")
        self.assertEqual(json.loads(out)["candidates"], [ident])
        code, out = self.call("prune", "--days", "90", "--at", LATER,
                             "--apply", "--json")
        self.assertEqual(json.loads(out)["moved"], [ident])

    def test_runbooks(self) -> None:
        code, out = self.call("runbooks")
        self.assertEqual(code, 0)
        names = out.split()
        self.assertIn("crash-loop", names)
        code, out = self.call("runbook", "crash-loop")
        self.assertEqual(code, 0)
        self.assertIn("崩溃循环处置", out)
        code, _ = self.call("runbook", "missing")
        self.assertEqual(code, 3)

    def test_default_actor_from_environment(self) -> None:
        ident = self.create()
        code, out = self.call("show", ident, "--json")
        self.assertEqual(json.loads(out)["timeline"][0]["actor"], "operator")


if __name__ == "__main__":
    unittest.main()
