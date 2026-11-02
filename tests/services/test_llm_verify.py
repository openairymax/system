# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 模型资产校验测试
#
# 校验层是清单与磁盘相遇的地方，反向用例比正向更重要：文件缺失、截断、
# 内容替换、符号链接越界四类都必须各自拒绝并给出不同归因，否则状态层
# 无法回答「为什么起不来」。

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyllm import model, verify  # noqa: E402
from airyllm.failure import Failure  # noqa: E402

BODY = b"airy-model-weight"


def asset(path: str, body: bytes) -> model.Asset:
    return model.Asset(path=path, size=len(body),
                       sha256=hashlib.sha256(body).hexdigest())


def entry(*assets: model.Asset) -> model.Manifest:
    runtime = model.Runtime(engine="llama.cpp", version="b4000",
                            path="/usr/libexec/airy-llm/llama-server")
    return model.Manifest(schema=model.SCHEMA_VERSION, name="airy-small",
                          version="1.0.0", quant="q4_k_m",
                          runtime=runtime, assets=assets)


class DigestTest(unittest.TestCase):
    """摘要必须与一次性计算逐字相同，跨读块边界也不得漏字节。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_known(self) -> None:
        path = self.dir / "a.bin"
        path.write_bytes(BODY)
        self.assertEqual(verify.digest(path), hashlib.sha256(BODY).hexdigest())

    def test_chunk_boundary(self) -> None:
        body = bytes(range(256)) * (verify.CHUNK // 128) + b"tail"
        path = self.dir / "big.bin"
        path.write_bytes(body)
        self.assertEqual(verify.digest(path), hashlib.sha256(body).hexdigest())

    def test_tiny_chunk(self) -> None:
        path = self.dir / "a.bin"
        path.write_bytes(BODY)
        self.assertEqual(verify.digest(path, 3),
                         hashlib.sha256(BODY).hexdigest())


class CheckTest(unittest.TestCase):
    """资产与清单必须逐件相符，任一偏离即拒绝且归因可分。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def place(self, name: str, body: bytes = BODY) -> model.Asset:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return asset(name, body)

    def test_pass(self) -> None:
        item = self.place("weight.gguf")
        verify.check(self.dir, entry(item))

    def test_nested(self) -> None:
        item = self.place("sub/dir/weight.gguf")
        verify.check(str(self.dir), entry(item))

    def test_multi(self) -> None:
        first = self.place("weight.gguf")
        second = self.place("config.json", b"{}")
        verify.check(self.dir, entry(first, second))

    def test_missing(self) -> None:
        with self.assertRaises(Failure) as caught:
            verify.check(self.dir, entry(asset("gone.gguf", BODY)))
        self.assertIs(caught.exception.reason, model.Reason.NO_MODEL)

    def test_truncated(self) -> None:
        item = self.place("weight.gguf")
        broken = model.Asset(path=item.path, size=item.size + 1,
                             sha256=item.sha256)
        with self.assertRaises(Failure) as caught:
            verify.check(self.dir, entry(broken))
        self.assertIs(caught.exception.reason, model.Reason.ASSET_CORRUPT)

    def test_replaced(self) -> None:
        item = self.place("weight.gguf")
        (self.dir / item.path).write_bytes(b"x" * item.size)
        with self.assertRaises(Failure) as caught:
            verify.check(self.dir, entry(item))
        self.assertIs(caught.exception.reason, model.Reason.DIGEST_MISMATCH)

    def test_digest(self) -> None:
        item = self.place("weight.gguf")
        broken = model.Asset(path=item.path, size=item.size, sha256="0" * 64)
        with self.assertRaises(Failure) as caught:
            verify.check(self.dir, entry(broken))
        self.assertIs(caught.exception.reason, model.Reason.DIGEST_MISMATCH)

    def test_symlink_escape(self) -> None:
        outer = tempfile.TemporaryDirectory()
        self.addCleanup(outer.cleanup)
        target = Path(outer.name) / "weight.gguf"
        target.write_bytes(BODY)
        (self.dir / "weight.gguf").symlink_to(target)
        item = asset("weight.gguf", BODY)
        with self.assertRaises(Failure) as caught:
            verify.check(self.dir, entry(item))
        self.assertIs(caught.exception.reason, model.Reason.ASSET_CORRUPT)

    def test_directory(self) -> None:
        (self.dir / "weight.gguf").mkdir()
        with self.assertRaises(Failure) as caught:
            verify.check(self.dir, entry(asset("weight.gguf", BODY)))
        self.assertIs(caught.exception.reason, model.Reason.NO_MODEL)

    def test_empty_list(self) -> None:
        verify.check(self.dir, entry())


if __name__ == "__main__":
    unittest.main()
