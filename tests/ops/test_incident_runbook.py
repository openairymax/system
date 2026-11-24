# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 复盘报告与运行手册测试
#
# 报告结构必须与运维分册第 8.1 节一致，能从台账算出的字段不得留空；
# 运行手册按名字取用，缺失时给出台账错误而不是空文档。

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))

from incident import runbook  # noqa: E402
from incident.model import (  # noqa: E402
    BadStore, Incident, Severity, State, make_id, day_key,
)

BASE = 1_700_000_000 * 1_000_000_000
NS = 1_000_000_000
DAY = 86400 * NS


def build(**kw) -> Incident:
    base = {
        "incident_id": make_id(day_key(BASE), 1),
        "severity": Severity.P0,
        "category": "system",
        "title": "macro_d 反复重启",
        "detected_at": BASE,
    }
    base.update(kw)
    return Incident(**base)


class SpanTest(unittest.TestCase):
    def test_formats_short_and_long(self) -> None:
        self.assertEqual(runbook.span_text(0), "00:00:00")
        self.assertEqual(runbook.span_text(3661 * NS), "01:01:01")
        self.assertEqual(runbook.span_text(DAY + 3661 * NS),
                         "1d 01:01:01")

    def test_report_id(self) -> None:
        self.assertEqual(runbook.report_id(build()), "PM-20231114-001")


class RenderTest(unittest.TestCase):
    def test_sections_follow_the_template(self) -> None:
        body = runbook.render(build(), "operator", BASE + 60 * NS)
        for head in ("## 元信息", "## 1. 事件摘要", "## 2. 影响分析",
                     "## 3. 时间线", "## 4. 根因分析", "### 4.1 触发原因",
                     "### 4.2 根本原因", "### 4.3 5 Whys 分析",
                     "## 5. 响应评估", "## 6. 改进措施", "## 7. 经验教训",
                     "## 8. 附录"):
            self.assertIn(head, body)

    def test_summary_is_derived_when_absent(self) -> None:
        inc = build(affected_agents=[1, 2], affected_daemons=["mem_d"])
        body = runbook.render(inc, "operator", BASE)
        self.assertIn("影响 2 个 Agent、1 个 daemon", body)

    def test_root_cause_is_not_duplicated(self) -> None:
        inc = build()
        body = runbook.render(inc, "operator", BASE, "",
                              "配置项缺失导致重启")
        self.assertIn("配置项缺失导致重启", body)
        self.assertEqual(body.count("配置项缺失导致重启"), 1)

    def test_timeline_renders_events(self) -> None:
        inc = build()
        inc.switch(State.TRIAGED, "operator", BASE + 5 * NS)
        body = runbook.render(inc, "operator", BASE + 10 * NS)
        self.assertIn("| T+00:00:05 | triaged | operator |", body)

    def test_bad_timeline_stamp_is_marked(self) -> None:
        inc = build()
        inc.timeline.append({"ts": "bad", "event": "manual", "actor": "op"})
        body = runbook.render(inc, "operator", BASE)
        self.assertIn("T+?", body)

    def test_span_uses_resolution_when_present(self) -> None:
        inc = build()
        inc.switch(State.TRIAGED, "operator", BASE + 1)
        inc.switch(State.RESPONDING, "operator", BASE + 2)
        inc.switch(State.RECOVERING, "operator", BASE + 3)
        inc.switch(State.RESOLVED, "operator", BASE + 60 * NS)
        body = runbook.render(inc, "operator", BASE + 60 * NS)
        self.assertIn("持续时长：00:01:00", body)


class LookupTest(unittest.TestCase):
    def test_names_are_present(self) -> None:
        names = runbook.names()
        for want in ("crash-loop", "security-violation",
                     "memory-pressure", "storage-exhausted"):
            self.assertIn(want, names)

    def test_text_returns_document(self) -> None:
        self.assertIn("崩溃循环处置", runbook.text("crash-loop"))

    def test_unknown_name_is_store_error(self) -> None:
        with self.assertRaises(BadStore):
            runbook.text("missing")


if __name__ == "__main__":
    unittest.main()
