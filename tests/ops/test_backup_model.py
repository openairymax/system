# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 备份台账模型测试
#
# 编号必须带类型前缀与当日序号；保留期按日历推进，周与月不能退化成
# 固定 7/30 天；归档路径只接受相对、无上跳。

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))

from backup.model import (  # noqa: E402
    BadInput, BadStore, BackupRecord, Kind, Retention, check_id, iso,
    agent_member, make_id, parse_id, parse_iso, safe_arcname,
    sum_sources,
)

BASE = 1_700_000_000 * 1_000_000_000
DAY = 86400 * 1_000_000_000


class IdTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        stamp = parse_iso("2026-07-18T02:00:00Z")
        ident = make_id(Kind.FULL, stamp, 1)
        self.assertEqual(ident, "FULL-20260718-001")
        kind, day, seq = parse_id(ident)
        self.assertIs(kind, Kind.FULL)
        self.assertEqual(day, "20260718")
        self.assertEqual(seq, 1)

    def test_rejects_malformed(self) -> None:
        for bad in ("", "FULL-2026-001", "full-20260718-001", "FULL-20260718"):
            with self.assertRaises(BadInput):
                check_id(bad)

    def test_iso_is_second_precision(self) -> None:
        stamp = parse_iso("2026-01-02T03:04:05Z")
        self.assertEqual(iso(stamp), "2026-01-02T03:04:05Z")

    def test_unknown_kind(self) -> None:
        with self.assertRaises(BadInput):
            Kind.parse("snapshot")


class RetentionTest(unittest.TestCase):
    def test_incremental_and_full(self) -> None:
        policy = Retention()
        self.assertEqual(policy.cutoff(Kind.INCREMENTAL, BASE), BASE - 7 * DAY)
        self.assertEqual(policy.cutoff(Kind.FULL, BASE), BASE - 30 * DAY)
        self.assertEqual(policy.cutoff(Kind.WEEKLY, BASE), BASE - 84 * DAY)

    def test_month_uses_calendar(self) -> None:
        policy = Retention(monthly_months=1)
        stamp = parse_iso("2026-03-31T00:00:00Z")
        cutoff = policy.cutoff(Kind.MONTHLY, stamp)
        self.assertEqual(iso(cutoff), "2026-02-28T00:00:00Z")


class PathTest(unittest.TestCase):
    def test_safe_arcname(self) -> None:
        self.assertEqual(safe_arcname("var/lib/airy/agents"), "var/lib/airy/agents")
        for bad in ("/etc/passwd", "../etc", "var//x", "var/./x", ""):
            with self.assertRaises(BadInput):
                safe_arcname(bad)

    def test_agent_member(self) -> None:
        self.assertTrue(agent_member("var/lib/airy/agents/7/a", 7))
        self.assertTrue(agent_member("var/lib/airy/memory/l3/7/x", "7"))
        self.assertTrue(agent_member("agents/7/a", 7))
        self.assertTrue(agent_member("memory/l4/7/x", 7))
        self.assertFalse(agent_member("var/lib/airy/agents/8/a", 7))
        self.assertFalse(agent_member("var/lib/airy/memory/l3/8/x", 7))
        self.assertFalse(agent_member("etc/agentrt/agentrt.toml", 7))


class RecordTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        stamp = parse_iso("2026-07-18T02:00:00Z")
        record = BackupRecord(
            backup_id=make_id(Kind.FULL, stamp, 1),
            kind=Kind.FULL,
            created_at=stamp,
            completed_at=stamp,
            file="full_20260718-001.tar.zst",
            compression="zstd",
            size_bytes=10,
            sha256="0" * 64,
            sources=[{"name": "agents", "files": 2}],
            agent_count=1,
        )
        restored = BackupRecord.from_dict(record.to_dict())
        self.assertEqual(restored, record)

    def test_rejects_bad_digest(self) -> None:
        with self.assertRaises(BadStore):
            BackupRecord(
                backup_id="FULL-20260718-001", kind=Kind.FULL, created_at=BASE,
                completed_at=BASE, file="x.tar.zst", compression="zstd",
                size_bytes=1, sha256="xyz",
            )

    def test_summarize(self) -> None:
        files, size, agents = sum_sources([
            {"name": "var/lib/airy/agents", "files": 2, "bytes": 10,
             "agent_count": 3},
            {"name": "etc/agentrt", "files": 1, "bytes": 5, "agent_count": 9},
        ])
        self.assertEqual((files, size, agents), (3, 15, 3))


if __name__ == "__main__":
    unittest.main()
