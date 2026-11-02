# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 升级检查状态测试
#
# 覆盖三条判据：写入是原子的（失败注入后旧文件不变、无残留临时文件）、
# 失败不覆盖 last_ok、状态损坏时报错而不是当成空状态。最后一条区分了
# 「没检查过」与「状态已坏」，两者对 status 的结论完全不同。

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyupdate import state  # noqa: E402


class RoundTripTest(unittest.TestCase):
    """写入再读出必须逐字段相等。"""

    def test_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            self.assertEqual(state.load(path), state.State())

    def test_full(self) -> None:
        item = state.State(
            last_ok=state.Finding("0.0.4", 3, "2026-11-02T00:00:00Z"),
            last_error=state.Failure("dns failure", "2026-11-01T00:00:00Z"),
            notified="0.0.4/3",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state.save(path, item)
            self.assertEqual(state.load(path), item)

    def test_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state.save(path, state.State())
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o644)

    def test_no_temp_left(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state.save(path, state.State())
            self.assertEqual(sorted(os.listdir(tmp)), ["state.json"])

    def test_trailing_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state.save(path, state.State())
            self.assertTrue(path.read_bytes().endswith(b"\n"))


class AtomicTest(unittest.TestCase):
    """替换失败时旧结论必须原样保留。"""

    def test_replace_failure(self) -> None:
        old = state.State(last_ok=state.Finding("0.0.4", 3, "2026-11-01T00:00:00Z"))
        new = state.State(last_ok=state.Finding("0.0.5", 1, "2026-11-02T00:00:00Z"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state.save(path, old)
            with mock.patch.object(state.os, "replace", side_effect=OSError):
                with self.assertRaises(OSError):
                    state.save(path, new)
            self.assertEqual(state.load(path), old)
            self.assertEqual(sorted(os.listdir(tmp)), ["state.json"])

    def test_reader_sees_whole_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state.save(path, state.State())
            raw = path.read_text(encoding="utf-8")
            self.assertEqual(json.loads(raw)["schema_version"], 1)


class TransitionTest(unittest.TestCase):
    """成功与失败各自只动自己的字段。"""

    def base(self) -> state.State:
        return state.State(
            last_ok=state.Finding("0.0.4", 19, "2026-11-01T00:00:00Z"),
            last_error=None,
            notified="0.0.4/19",
        )

    def test_error_keeps_ok(self) -> None:
        got = state.record_error(
            self.base(),
            state.Failure("network unreachable", "2026-11-02T00:00:00Z"),
        )
        self.assertEqual(got.last_ok, self.base().last_ok)
        self.assertEqual(got.last_error.reason, "network unreachable")

    def test_ok_keeps_error(self) -> None:
        item = state.record_error(
            self.base(),
            state.Failure("dns failure", "2026-11-01T01:00:00Z"),
        )
        got = state.record_ok(
            item,
            state.Finding("0.0.5", 1, "2026-11-02T00:00:00Z"),
        )
        self.assertEqual(got.last_error.reason, "dns failure")
        self.assertEqual(got.last_ok.version, "0.0.5")

    def test_sent_keeps_ok(self) -> None:
        got = state.record_sent(self.base(), "0.0.5/1")
        self.assertEqual(got.last_ok, self.base().last_ok)
        self.assertEqual(got.notified, "0.0.5/1")


class CorruptTest(unittest.TestCase):
    """损坏的状态不能被静默当成「从未检查」。"""

    def decode(self, text: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(state.Corrupt):
                state.load(path)

    def test_not_json(self) -> None:
        self.decode("{")

    def test_shape(self) -> None:
        self.decode(json.dumps({"schema_version": 1}))

    def test_schema(self) -> None:
        self.decode(json.dumps({
            "schema_version": 2,
            "last_ok": None,
            "last_error": None,
            "notified": None,
        }))

    def test_ok_shape(self) -> None:
        self.decode(json.dumps({
            "schema_version": 1,
            "last_ok": {"version": "0.0.4", "build": True,
                        "checked_at": "2026-11-02T00:00:00Z"},
            "last_error": None,
            "notified": None,
        }))

    def test_notified_type(self) -> None:
        self.decode(json.dumps({
            "schema_version": 1,
            "last_ok": None,
            "last_error": None,
            "notified": 3,
        }))


if __name__ == "__main__":
    unittest.main()
