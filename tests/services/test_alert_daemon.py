# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 告警守护端到端测试
#
# 守护进程与命令行在同一个临时目录里对跑：套接字就绪、事件落地、活动列表、
# 归档查询与确认都要走真实进程边界，同时覆盖坏规则、坏参数与不可达三条
# 失败路径的退出码。

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DAEMON = ROOT / "bin" / "airy-alertd"
CLIENT = ROOT / "bin" / "airy-alert"


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.rules = self.base / "rules"
        self.rules.mkdir()
        (self.rules / "10-mute.json").write_text(json.dumps({
            "schema": 1,
            "rules": [{
                "name": "mute_info",
                "window": "1m",
                "max_emit": 0,
                "match": {"source": "macro_d", "severity": "INFO"},
            }],
        }), encoding="utf-8")
        self.state = self.base / "state"
        self.sock = self.base / "alert.sock"
        self.proc = subprocess.Popen(
            [sys.executable, str(DAEMON), "--socket", str(self.sock),
             "--rules", str(self.rules), "--state", str(self.state)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop)
        self.wait_ready()

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(10)
        for stream in (self.proc.stdout, self.proc.stderr):
            if stream is not None:
                stream.close()

    def wait_ready(self) -> None:
        limit = time.time() + 10
        while time.time() < limit:
            if self.sock.exists():
                return
            if self.proc.poll() is not None:
                _out, err = self.proc.communicate()
                self.fail(f"daemon exited early: {err.strip()}")
            time.sleep(0.05)
        self.fail("daemon socket not ready")

    def cli(self, *args: str) -> tuple[int, list[dict]]:
        done = subprocess.run(
            [sys.executable, str(CLIENT), *args],
            capture_output=True, text=True, timeout=30)
        rows = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
        return done.returncode, rows

    def send(self, severity: str = "WARNING", code: int = 7, *extra: str):
        return self.cli("send", "--socket", str(self.sock),
                        "--severity", severity, "--code", str(code), *extra)

    def listing(self) -> list[dict]:
        rc, rows = self.cli("list", "--state", str(self.state))
        self.assertEqual(rc, 0)
        return rows


class LifecycleTest(Base):
    def test_send_list_ack_history(self) -> None:
        rc, rows = self.send("WARNING", 7, "--message", "disk slow")
        self.assertEqual(rc, 0)
        self.assertEqual(rows[-1]["action"], "EMIT")

        rc, rows = self.cli("status", "--state", str(self.state))
        self.assertEqual(rc, 0)
        self.assertEqual(rows[-1]["active"], 1)

        active = self.listing()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["code"], 7)
        self.assertEqual(active[0]["severity"], "WARNING")
        alert_id = active[0]["alert_id"]

        rc, rows = self.cli("ack", alert_id, "--socket", str(self.sock),
                            "--operator", "ops", "--comment", "checked")
        self.assertEqual(rc, 0)
        self.assertEqual(rows[-1]["action"], "ACK")
        self.assertEqual(self.listing(), [])

        rc, rows = self.cli("history", "--state", str(self.state))
        self.assertEqual(rc, 0)
        self.assertTrue(any(row["state"] == "RESOLVED" for row in rows))
        self.assertTrue(all(row["code"] == 7 for row in rows))

    def test_recover_then_ack(self) -> None:
        self.send("CRITICAL", 9, "--message", "link down")
        alert_id = self.listing()[0]["alert_id"]
        rc, rows = self.cli("recover", "--socket", str(self.sock),
                            "--severity", "CRITICAL", "--source", "macro_d",
                            "--code", "9")
        self.assertEqual(rc, 0)
        self.assertEqual(rows[-1]["action"], "PENDING")
        self.assertEqual(self.listing()[0]["state"], "PENDING_RESOLVE")

        rc, rows = self.cli("ack", alert_id, "--socket", str(self.sock))
        self.assertEqual(rc, 0)
        self.assertEqual(rows[-1]["action"], "ACK")

    def test_info_rule_suppressed(self) -> None:
        rc, rows = self.send("INFO", 0, "--message", "heartbeat")
        self.assertEqual(rc, 0)
        self.assertEqual(rows[-1]["action"], "SUPPRESS")
        self.assertEqual(self.listing(), [])

    def test_status_without_state(self) -> None:
        rc, rows = self.cli("status", "--state", str(self.base / "absent"))
        self.assertEqual(rc, 3)
        self.assertIn("error", rows[-1])

    def test_history_limit_rejected(self) -> None:
        rc, rows = self.cli("history", "--state", str(self.state), "--limit", "0")
        self.assertEqual(rc, 2)
        self.assertIn("limit", rows[-1]["error"])


class FailureTest(Base):
    def test_unknown_alert_id(self) -> None:
        rc, rows = self.cli("ack", "deadbeef", "--socket", str(self.sock))
        self.assertEqual(rc, 4)
        self.assertEqual(rows[-1]["action"], "IGNORE")

    def test_bad_code_rejected_by_daemon(self) -> None:
        rc, rows = self.send("WARNING", -1)
        self.assertEqual(rc, 4)
        self.assertIn("error", rows[-1])

    def test_bad_label_rejected(self) -> None:
        rc, rows = self.send("WARNING", 1, "--label", "broken")
        self.assertEqual(rc, 3)
        self.assertIn("error", rows[-1])

    def test_unreachable_socket(self) -> None:
        rc, rows = self.cli(
            "send", "--socket", str(self.base / "absent.sock"),
            "--severity", "INFO")
        self.assertEqual(rc, 3)
        self.assertIn("unreachable", rows[-1]["error"])


class StartupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def run_daemon(self, rules: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(DAEMON), "--socket", str(self.base / "a.sock"),
             "--rules", str(rules), "--state", str(self.base / "state"),
             "--once"],
            capture_output=True, text=True, timeout=30)

    def test_missing_rules_exit_3(self) -> None:
        done = self.run_daemon(self.base / "absent")
        self.assertEqual(done.returncode, 3)
        self.assertIn("FATAL", done.stderr)

    def test_bad_rules_exit_3(self) -> None:
        rules = self.base / "rules"
        rules.mkdir()
        (rules / "10-bad.json").write_text("{oops", encoding="utf-8")
        done = self.run_daemon(rules)
        self.assertEqual(done.returncode, 3)
        self.assertIn("FATAL", done.stderr)


if __name__ == "__main__":
    unittest.main()
