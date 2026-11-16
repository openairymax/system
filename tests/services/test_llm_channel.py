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

ENGINE = "bin/llama-server"

ENGINE_BODY = b"elf-engine"

MARK = "bin/ENGINE_VERSION"

MARK_BODY = b"b4600\n"


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

    def engine_package(self) -> None:
        """引擎与权重同包的形态：引擎路径落在运行根内。

        包内文件的相对位置与运行根一一对应，落位后清单声明的绝对路径
        就是真的可执行文件；版本标记与引擎同目录，由打包方写入。
        """
        payload = sample()
        payload["runtime"]["path"] = f"{model.ROOT}/{ENGINE}"
        files = {ENGINE: ENGINE_BODY, MARK: MARK_BODY, ASSET: BODY}
        (self.src / "bin").mkdir(parents=True)
        payload["assets"] = []
        for path, body in files.items():
            (self.src / path).write_bytes(body)
            payload["assets"].append({
                "path": path,
                "size": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            })
        self.write(payload)

    def test_engine_asset_is_executable(self) -> None:
        self.engine_package()
        _, copied = channel.apply(self.src, self.home)
        self.assertEqual(copied, 3)
        self.assertEqual((self.home / ASSET).stat().st_mode & 0o777, 0o644)
        self.assertEqual((self.home / MARK).stat().st_mode & 0o777, 0o644)
        self.assertEqual((self.home / ENGINE).stat().st_mode & 0o777,
                         channel.RUN_MODE)

    def test_engine_mode_is_repaired(self) -> None:
        self.engine_package()
        channel.apply(self.src, self.home)
        (self.home / ENGINE).chmod(0o644)
        _, copied = channel.apply(self.src, self.home)
        self.assertEqual(copied, 1)
        self.assertEqual((self.home / ENGINE).stat().st_mode & 0o777,
                         channel.RUN_MODE)

    def test_system_engine_keeps_assets_read_only(self) -> None:
        channel.apply(self.src, self.home)
        self.assertEqual((self.home / ASSET).stat().st_mode & 0o777, 0o644)

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
