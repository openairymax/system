# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 归档层测试
#
# 往返必须保留目录结构与文件内容；编码是事实，清单里记什么就必须用
# 什么解；含上跳成员的归档必须在解包前被判为结构问题。

from __future__ import annotations

import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))

from backup.archive import (  # noqa: E402
    choose_codec, inspect, pack, source_stats, unpack, zstd_available,
)
from backup.model import BadIntegrity  # noqa: E402


class ArchiveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.src = self.root / "src"
        (self.src / "agents" / "7").mkdir(parents=True)
        (self.src / "agents" / "7" / "state.json").write_text(
            '{"agent": 7}\n', encoding="utf-8")
        (self.src / "agents" / "7" / "notes").mkdir()
        (self.src / "agents" / "7" / "notes" / "a.txt").write_text(
            "hello\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_source_stats(self) -> None:
        files, size, children = source_stats(self.src)
        self.assertEqual(files, 2)
        self.assertGreater(size, 0)
        self.assertEqual(children, 1)

    def test_roundtrip_gzip(self) -> None:
        out = self.root / "a.tar.gz"
        entries = pack([("agents", self.src / "agents")], out, codec="gzip")
        self.assertEqual(entries[0]["files"], 2)
        target = self.root / "out"
        self.assertEqual(unpack(out, "gzip", target), 2)
        self.assertEqual(
            (target / "agents" / "7" / "state.json").read_text().strip(),
            '{"agent": 7}')

    def test_roundtrip_zstd_when_available(self) -> None:
        if not zstd_available():
            self.skipTest("zstd 不可用")
        out = self.root / "a.tar.zst"
        pack([("agents", self.src / "agents")], out, codec="zstd")
        target = self.root / "out"
        unpack(out, "zstd", target)
        self.assertTrue((target / "agents" / "7" / "notes" / "a.txt").is_file())

    def test_codec_fallback_is_reported(self) -> None:
        codec = choose_codec("zstd")
        self.assertIn(codec, ("zstd", "gzip"))

    def test_traversal_member_is_rejected(self) -> None:
        bad = self.root / "bad.tar.gz"
        with tarfile.open(bad, "w:gz") as tar:
            info = tarfile.TarInfo("../evil")
            payload = b"x"
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        files, problems = inspect(bad, "gzip")
        self.assertEqual(files, 0)
        self.assertTrue(problems)
        with self.assertRaises(BadIntegrity):
            unpack(bad, "gzip", self.root / "out")
        self.assertFalse((self.root / "evil").exists())

    def test_filter_keeps_one_agent(self) -> None:
        agents = self.src / "agents"
        (agents / "8").mkdir()
        (agents / "8" / "x.txt").write_text("x", encoding="utf-8")
        out = self.root / "a.tar.gz"
        pack([("agents", agents)], out, codec="gzip")
        target = self.root / "out"
        files = unpack(out, "gzip", target,
                       keep=lambda name: "/7/" in name or name.endswith("/7"))
        self.assertEqual(files, 2)
        self.assertTrue((target / "agents" / "7").is_dir())
        self.assertFalse((target / "agents" / "8").exists())


if __name__ == "__main__":
    unittest.main()
