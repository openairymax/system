# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# Agent 日志读取与归档测试
#
# tail 必须横跨普通日志与 gzip；归档只搬超过保留期的文件；组权限不满足
# 必须抛 BadPerm，而不是悄悄返回空结果。

from __future__ import annotations

import gzip
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))
sys.path.insert(0, str(ROOT / "services"))

from agent import logs  # noqa: E402
from agent.model import BadPerm  # noqa: E402


class LogsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "logs"
        self.folder = self.root / "1"
        self.folder.mkdir(parents=True)
        self.now = time.time_ns()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_access_injection(self) -> None:
        with patch.dict(os.environ, {"AIRY_AGENT_GROUPS": "airy-log"}):
            logs.ensure_access()
        with patch.dict(os.environ, {"AIRY_AGENT_GROUPS": "users"}):
            with self.assertRaises(BadPerm):
                logs.ensure_access()

    def test_tail_across_plain_and_gzip(self) -> None:
        (self.folder / "a.log").write_text("1\n2\n3\n", encoding="utf-8")
        with gzip.open(self.folder / "b.log.gz", "wt",
                       encoding="utf-8") as fh:
            fh.write("4\n5\n6\n")
        self.assertEqual(logs.tail_lines(self.root, "1", 4),
                         ["3", "4", "5", "6"])
        self.assertEqual(logs.tail_lines(self.root, "9", 4), [])

    def test_archive_only_expired(self) -> None:
        old = self.folder / "old.log"
        old.write_text("old\n", encoding="utf-8")
        fresh = self.folder / "fresh.log"
        fresh.write_text("fresh\n", encoding="utf-8")
        stale = time.time() - 40 * 86400
        os.utime(old, (stale, stale))
        target = self.base / "archive"
        moved = logs.archive_logs(self.root, target, "1", 30, self.now)
        self.assertEqual(moved, ["old.log.gz"])
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        with gzip.open(target / "1" / "old.log.gz", "rt",
                       encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "old\n")

    def test_recent_errors_window(self) -> None:
        self.assertIsNone(logs.recent_errors(self.root, "1", self.now))
        path = self.folder / "a.log"
        path.write_text(
            '{"ts_ns": %d, "level": "ERROR"}\n'
            '{"ts_ns": %d, "level": "INFO"}\n'
            '{"ts_ns": %d, "level": "FATAL"}\n' % (
                self.now - 1000, self.now - 1000, self.now - 90 * 10**9),
            encoding="utf-8")
        self.assertEqual(logs.recent_errors(self.root, "1", self.now), 1)


if __name__ == "__main__":
    unittest.main()
