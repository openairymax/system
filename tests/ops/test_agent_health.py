# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# Agent 健康评分测试
#
# 六因素权重、四档等级与 unknown 降权是三件必须可验证的事：已知因素是
# 真实 /proc 与 meta 数据，缺失因素必须出现在 unknown 里而不是零分。

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))
sys.path.insert(0, str(ROOT / "services"))

from agent import health  # noqa: E402
from agent.model import Agent, State, parse_iso  # noqa: E402

STAMP = parse_iso("2026-03-04T05:06:07Z")


def make(**kw):
    base = {
        "id": "1",
        "name": "bot",
        "template": "chat",
        "state": State.RUNNING,
        "created_at": STAMP,
        "updated_at": STAMP,
        "token": {"total_budget": 1000, "consumed": 0,
                  "reset_period": "manual", "last_reset_at": ""},
        "memory": {"l1_mb": 1024, "l2_mb": 3072},
        "sched": {"runtime_ms": 100, "deadline_ms": 100,
                  "period_ms": 100},
    }
    base.update(kw)
    return Agent(**base)


class GradeTest(unittest.TestCase):
    def test_four_bands(self) -> None:
        self.assertEqual(health.grade_of(100), "HEALTHY")
        self.assertEqual(health.grade_of(80), "HEALTHY")
        self.assertEqual(health.grade_of(79), "DEGRADED")
        self.assertEqual(health.grade_of(50), "DEGRADED")
        self.assertEqual(health.grade_of(49), "CRITICAL")
        self.assertEqual(health.grade_of(1), "CRITICAL")
        self.assertEqual(health.grade_of(0), "DEAD")


class FactorTest(unittest.TestCase):
    def test_heartbeat_penalty(self) -> None:
        self.assertEqual(
            health._heartbeat(make(heartbeat_timeouts=0))["score"], 100.0)
        row = health._heartbeat(make(heartbeat_timeouts=30))
        self.assertEqual(row["score"], 0.0)

    def test_cpu_quota_penalty(self) -> None:
        agent = make()
        self.assertEqual(
            health._cpu(agent, {"cpu_pct": 50.0, "rss_mb": 10})["score"],
            100.0)
        self.assertEqual(
            health._cpu(agent, {"cpu_pct": 95.0, "rss_mb": 10})["score"],
            95.0)

    def test_memory_penalty(self) -> None:
        agent = make()
        self.assertEqual(
            health._memory(agent, {"cpu_pct": 1.0, "rss_mb": 2048})["score"],
            100.0)
        self.assertEqual(
            health._memory(agent, {"cpu_pct": 1.0, "rss_mb": 4096})["score"],
            90.0)

    def test_ipc_and_token_penalty(self) -> None:
        self.assertEqual(health._ipc(make(ipc_p99_ms=5))["score"], 100.0)
        self.assertEqual(health._ipc(make(ipc_p99_ms=15))["score"], 0.0)
        spent = make(token={"total_budget": 1000, "consumed": 1000,
                            "reset_period": "manual", "last_reset_at": ""})
        self.assertEqual(health._token(spent)["score"], 0.0)


class EvaluateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.log_root = Path(self.tmp.name)
        self.now = time.time_ns()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _log(self, ident: str, stamp: int, level: str = "ERROR") -> None:
        folder = self.log_root / ident
        folder.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts_ns": stamp, "level": level, "msg": "x"})
        with (folder / "today.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def test_all_sources_known(self) -> None:
        self._log("1", self.now)
        agent = make(pid=os.getpid(), heartbeat_timeouts=0, ipc_p99_ms=2)
        doc = health.evaluate(agent, self.log_root, self.now)
        self.assertEqual(doc["coverage_pct"], 100.0)
        self.assertEqual(doc["unknown"], [])
        self.assertIsInstance(doc["score"], int)
        self.assertIn(doc["grade"], {"HEALTHY", "DEGRADED", "CRITICAL"})

    def test_missing_sources_marked_unknown(self) -> None:
        agent = make()
        doc = health.evaluate(agent, self.log_root, self.now)
        self.assertEqual(doc["unknown"],
                         ["heartbeat", "cpu", "memory", "ipc", "errors"])
        self.assertEqual(doc["coverage_pct"], 10.0)
        self.assertEqual(doc["grade"], "UNKNOWN")
        self.assertTrue(doc["renormalized"])

    def test_error_window_and_level(self) -> None:
        self._log("1", self.now - 90 * 1_000_000_000)
        self._log("1", self.now, level="INFO")
        agent = make()
        row = health._errors(agent, self.log_root, self.now)
        self.assertTrue(row["known"])
        self.assertEqual(row["score"], 100.0)
        self._log("1", self.now)
        self.assertEqual(
            health._errors(agent, self.log_root, self.now)["score"], 100.0)


if __name__ == "__main__":
    unittest.main()
