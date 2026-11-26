# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# Agent 生命周期模型测试
#
# 8 态与合法迁移边是运维安全边界：非法迁移必须抛 BadState，不能被静默
# 纠正；输入单位解析失败必须抛 BadInput。

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))
sys.path.insert(0, str(ROOT / "services"))

from agent.model import (  # noqa: E402
    Agent, BadInput, BadState, State, check_id, parse_iso, parse_mb,
    parse_ms, split_caps,
)

STAMP = parse_iso("2026-01-02T03:04:05Z")


def make(**kw):
    base = {
        "id": "1",
        "name": "bot",
        "template": "chat",
        "state": State.INACTIVE,
        "created_at": STAMP,
        "updated_at": STAMP,
        "token": {"total_budget": 1000, "consumed": 100,
                  "reset_period": "manual", "last_reset_at": ""},
        "memory": {"l1_mb": 256, "l2_mb": 1024},
        "sched": {"runtime_ms": 10, "deadline_ms": 30, "period_ms": 100},
    }
    base.update(kw)
    return Agent(**base)


class StateTest(unittest.TestCase):
    def test_full_start_sequence(self) -> None:
        agent = make()
        for target in (State.SPAWNING, State.READY, State.RUNNING):
            agent.switch(target, "start", STAMP)
        self.assertIs(agent.state, State.RUNNING)
        self.assertEqual(len(agent.transitions), 3)

    def test_pause_and_resume(self) -> None:
        agent = make(state=State.RUNNING)
        agent.switch(State.BLOCKED, "pause", STAMP)
        agent.switch(State.RUNNING, "resume", STAMP)
        self.assertIs(agent.state, State.RUNNING)

    def test_terminal_dead_rejects_all(self) -> None:
        agent = make(state=State.DEAD)
        with self.assertRaises(BadState):
            agent.switch(State.RUNNING, "start", STAMP)

    def test_illegal_shortcut_rejected(self) -> None:
        agent = make()
        with self.assertRaises(BadState):
            agent.switch(State.RUNNING, "start", STAMP)

    def test_illegal_stop_from_inactive(self) -> None:
        agent = make()
        with self.assertRaises(BadState):
            agent.switch(State.STOPPING, "stop", STAMP)

    def test_history_is_bounded(self) -> None:
        agent = make(state=State.RUNNING)
        for _ in range(250):
            agent.switch(State.BLOCKED, "pause", STAMP)
            agent.switch(State.RUNNING, "resume", STAMP)
        self.assertLessEqual(len(agent.transitions), 200)


class ParseTest(unittest.TestCase):
    def test_memory_units(self) -> None:
        self.assertEqual(parse_mb("256MB"), 256)
        self.assertEqual(parse_mb("1GB"), 1024)
        self.assertEqual(parse_mb("512"), 512)

    def test_time_units(self) -> None:
        self.assertEqual(parse_ms("10ms"), 10)
        self.assertEqual(parse_ms("1s"), 1000)

    def test_rejects_zero_and_garbage(self) -> None:
        for value in ("0MB", "-1MB", "abc", ""):
            with self.assertRaises(BadInput):
                parse_mb(value)
        for value in ("0ms", "x"):
            with self.assertRaises(BadInput):
                parse_ms(value)

    def test_ids(self) -> None:
        self.assertEqual(check_id("007"), "7")
        for value in ("0", "-1", "abc", ""):
            with self.assertRaises(BadInput):
                check_id(value)

    def test_capabilities(self) -> None:
        self.assertEqual(split_caps("llm, http,llm"), ["llm", "http"])
        with self.assertRaises(BadInput):
            split_caps(" ")

    def test_sched_order_enforced(self) -> None:
        with self.assertRaises(BadInput):
            make(sched={"runtime_ms": 40, "deadline_ms": 30,
                        "period_ms": 100})
        with self.assertRaises(BadInput):
            make(memory={"l1_mb": 2048, "l2_mb": 1024})


class JsonTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        agent = make(capabilities=["llm"], pid=7, heartbeat_timeouts=1,
                     ipc_p99_ms=2.5, auto_tune=True)
        again = Agent.from_json(agent.to_json())
        self.assertEqual(again.to_json(), agent.to_json())

    def test_token_math(self) -> None:
        agent = make()
        self.assertEqual(agent.remaining(), 900)
        self.assertEqual(agent.used_pct(), 10.0)
        self.assertGreaterEqual(agent.rate(STAMP + 1000 * 1_000_000), 0.0)


if __name__ == "__main__":
    unittest.main()
