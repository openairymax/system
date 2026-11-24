# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 备份仓库测试
#
# 摘要不符时恢复必须在解包前停住；全量恢复要显式确认；保留期清理
# 任何时候都留一份最新的全量。

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))

from backup.model import (  # noqa: E402
    BadInput, BadIntegrity, Kind, parse_iso,
)
from backup.store import Store, sha256_file  # noqa: E402

BASE = parse_iso("2026-07-18T02:00:00Z")
DAY = 86400 * 1_000_000_000


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.repo = self.base / "repo"
        self.src = self.base / "src"
        for agent in ("7", "8"):
            (self.src / "var/lib/airy/agents" / agent).mkdir(parents=True)
            (self.src / "var/lib/airy/agents" / agent / "state.json").write_text(
                f'{{"agent": {agent}}}\n', encoding="utf-8")
        (self.src / "var/lib/airy/memory/l3/7").mkdir(parents=True)
        (self.src / "var/lib/airy/memory/l3/7/m.txt").write_text(
            "memory\n", encoding="utf-8")
        self.store = Store(self.repo)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def sources(self) -> list[tuple[str, Path]]:
        return [
            ("var/lib/airy/agents", self.src / "var/lib/airy/agents"),
            ("var/lib/airy/memory/l3", self.src / "var/lib/airy/memory/l3"),
        ]

    def test_create_layout_and_verify(self) -> None:
        rec = self.store.create(Kind.FULL, self.sources(), stamp=BASE,
                                actor="tester", codec="gzip")
        self.assertEqual(rec.backup_id, "FULL-20260718-001")
        self.assertEqual(rec.agent_count, 2)
        self.assertEqual(rec.total_files, 3)

        archive_path = self.store.path_of(rec)
        self.assertTrue(archive_path.is_file())
        self.assertTrue(archive_path.with_name(rec.file + ".sha256").is_file())
        manifests = list(archive_path.parent.glob("*.manifest.json"))
        self.assertEqual(len(manifests), 1)
        self.assertIn(rec.backup_id, manifests[0].read_text(encoding="utf-8"))
        self.assertEqual(sha256_file(archive_path), rec.sha256)

        index = json.loads(self.store.index_path.read_text(encoding="utf-8"))
        self.assertEqual(index["backups"][0]["backup_id"], rec.backup_id)

        result = self.store.verify(rec.backup_id, deep=True)
        self.assertTrue(result.ok, result.errors)

    def test_sequence_within_day(self) -> None:
        first = self.store.create(Kind.FULL, self.sources(), stamp=BASE,
                                  codec="gzip")
        second = self.store.create(Kind.FULL, self.sources(), stamp=BASE,
                                   codec="gzip")
        self.assertEqual(first.backup_id, "FULL-20260718-001")
        self.assertEqual(second.backup_id, "FULL-20260718-002")
        self.assertEqual(second.previous_backup_id, first.backup_id)

    def test_tamper_blocks_restore(self) -> None:
        rec = self.store.create(Kind.FULL, self.sources(), stamp=BASE,
                                codec="gzip")
        with open(self.store.path_of(rec), "ab") as handle:
            handle.write(b"tampered")

        result = self.store.verify(rec.backup_id, deep=True)
        self.assertFalse(result.ok)
        self.assertIn("sha256", " ".join(result.errors))

        target = self.base / "out"
        with self.assertRaises(BadIntegrity):
            self.store.restore(rec.backup_id, target, confirm=True,
                               log_path=self.base / "rec.log")
        self.assertFalse(target.exists())

    def test_restore_requires_confirm_for_full(self) -> None:
        rec = self.store.create(Kind.FULL, self.sources(), stamp=BASE,
                                codec="gzip")
        with self.assertRaises(BadInput):
            self.store.restore(rec.backup_id, self.base / "out",
                               log_path=self.base / "rec.log")

    def test_restore_and_log(self) -> None:
        rec = self.store.create(Kind.FULL, self.sources(), stamp=BASE,
                                codec="gzip")
        target = self.base / "out"
        log = self.base / "rec.log"
        record = self.store.restore(rec.backup_id, target, confirm=True,
                                    actor="tester", log_path=log)
        self.assertEqual(record["files"], 3)
        restored = target / "var/lib/airy/agents/7/state.json"
        self.assertEqual(restored.read_text().strip(), '{"agent": 7}')
        entry = json.loads(log.read_text(encoding="utf-8").strip())
        self.assertEqual(entry["event"], "restore")
        self.assertEqual(entry["backup_id"], rec.backup_id)

    def test_restore_single_agent(self) -> None:
        rec = self.store.create(Kind.FULL, self.sources(), stamp=BASE,
                                codec="gzip")
        target = self.base / "one"
        record = self.store.restore(rec.backup_id, target, confirm=True,
                                    agent_id=7, log_path=self.base / "rec.log")
        self.assertEqual(record["files"], 2)
        self.assertTrue((target / "var/lib/airy/agents/7").is_dir())
        self.assertFalse((target / "var/lib/airy/agents/8").exists())

    def test_restore_overwrites_existing(self) -> None:
        rec = self.store.create(Kind.FULL, self.sources(), stamp=BASE,
                                codec="gzip")
        target = self.base / "out"
        old = target / "var/lib/airy/agents/7/state.json"
        old.parent.mkdir(parents=True)
        old.write_text("old\n", encoding="utf-8")
        self.store.restore(rec.backup_id, target, confirm=True,
                           log_path=self.base / "rec.log")
        self.assertEqual(old.read_text().strip(), '{"agent": 7}')

    def test_prune_keeps_latest_full(self) -> None:
        old_full = self.store.create(Kind.FULL, self.sources(), stamp=BASE,
                                     codec="gzip")
        old_inc = self.store.create(Kind.INCREMENTAL, self.sources(), stamp=BASE,
                                    codec="gzip")
        new_full = self.store.create(Kind.FULL, self.sources(),
                                     stamp=BASE + 40 * DAY, codec="gzip")

        preview = self.store.prune(stamp=BASE + 40 * DAY)
        self.assertIn(old_full.backup_id, preview["candidates"])
        self.assertIn(old_inc.backup_id, preview["candidates"])
        self.assertNotIn(new_full.backup_id, preview["candidates"])
        self.assertEqual(preview["removed"], [])

        result = self.store.prune(stamp=BASE + 40 * DAY, apply=True)
        self.assertEqual(sorted(result["removed"]),
                         sorted([old_full.backup_id, old_inc.backup_id]))
        self.assertEqual(self.store.ids(), [new_full.backup_id])
        self.assertFalse(self.store.path_of(old_full).exists())

    def test_missing_checksum_file_fails(self) -> None:
        rec = self.store.create(Kind.INCREMENTAL, self.sources(), stamp=BASE,
                                codec="gzip")
        self.store.path_of(rec).with_name(rec.file + ".sha256").unlink()
        result = self.store.verify(rec.backup_id)
        self.assertFalse(result.ok)
        self.assertIn("checksum_file", " ".join(result.errors))


if __name__ == "__main__":
    unittest.main()
