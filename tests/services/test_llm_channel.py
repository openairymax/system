# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 模型资产通道测试
#
# 通道的三个不变量：源不可信时不写目标、重跑同一份离线包无副作用、目标
# 被篡改后能收敛回源内容。第三条尤其重要——只做「复制一次」的通道在介质
# 写入不完整时无法自愈，用户只能看到一个摘要不符的状态。

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyllm import channel, model  # noqa: E402
from airyllm.failure import Failure  # noqa: E402

BODY = b"weight-bytes"

ASSET = "weights/model.gguf"


def sample(digest: str | None = None, size: int | None = None) -> dict:
    return {
        "schema": model.SCHEMA_VERSION,
        "name": "airy-small",
        "version": "1.0.0",
        "quant": "q4_k_m",
        "runtime": {
            "engine": "llama.cpp",
            "version": "b4000",
            "path": "/usr/bin/llama-server",
            "backends": ["cpu"],
        },
        "assets": [{
            "path": ASSET,
            "size": len(BODY) if size is None else size,
            "sha256": digest or hashlib.sha256(BODY).hexdigest(),
        }],
    }


class Case(unittest.TestCase):
    """每个用例自备离线包与运行根，互不共享磁盘。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.src = base / "bundle"
        self.home = base / "root"
        (self.src / "weights").mkdir(parents=True)
        (self.src / ASSET).write_bytes(BODY)
        self.write(sample())

    def write(self, payload: dict) -> None:
        text = json.dumps(payload)
        (self.src / "manifest.json").write_text(text, encoding="utf-8")

    def test_install_copies_nested_asset(self) -> None:
        entry, copied = channel.apply(self.src, self.home)
        self.assertEqual(copied, 1)
        self.assertEqual(entry.name, "airy-small")
        self.assertEqual((self.home / ASSET).read_bytes(), BODY)
        self.assertTrue((self.home / "manifest.json").is_file())

    def test_reapply_is_idempotent(self) -> None:
        channel.apply(self.src, self.home)
        _, copied = channel.apply(self.src, self.home)
        self.assertEqual(copied, 0)

    def test_tampered_asset_is_repaired(self) -> None:
        channel.apply(self.src, self.home)
        (self.home / ASSET).write_bytes(b"corrupt")
        _, copied = channel.apply(self.src, self.home)
        self.assertEqual(copied, 1)
        self.assertEqual((self.home / ASSET).read_bytes(), BODY)

    def test_corrupt_source_writes_nothing(self) -> None:
        (self.src / ASSET).write_bytes(b"short")
        with self.assertRaises(Failure) as caught:
            channel.apply(self.src, self.home)
        self.assertIs(caught.exception.reason, model.Reason.ASSET_CORRUPT)
        self.assertFalse((self.home / "manifest.json").exists())
        self.assertFalse((self.home / ASSET).exists())

    def test_missing_manifest_is_rejected(self) -> None:
        (self.src / "manifest.json").unlink()
        with self.assertRaises(Failure) as caught:
            channel.apply(self.src, self.home)
        self.assertIs(caught.exception.reason, model.Reason.NO_MANIFEST)

    def test_symlinked_destination_is_replaced(self) -> None:
        elsewhere = Path(self.tmp.name) / "elsewhere.gguf"
        elsewhere.write_bytes(BODY)
        (self.home / "weights").mkdir(parents=True)
        (self.home / ASSET).symlink_to(elsewhere)
        _, copied = channel.apply(self.src, self.home)
        self.assertEqual(copied, 1)
        self.assertFalse((self.home / ASSET).is_symlink())


if __name__ == "__main__":
    unittest.main()
