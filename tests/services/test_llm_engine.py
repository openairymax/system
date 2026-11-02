# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 模型运行判定测试
#
# 判定顺序是本层的核心不变量：清单、资产、后端三者任一不过，结果都必须
# 是失败或降级，且落盘状态与返回值一致。失败路径不写状态，外部工具就
# 只剩日志可看，故每个失败用例都同时断言状态文件。

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyllm import engine, model, status  # noqa: E402

BODY = b"weight-bytes"


def sample(engine: str, backends: tuple[str, ...] = ("cuda", "cpu"),
           digest: str | None = None) -> dict:
    return {
        "schema": model.SCHEMA_VERSION,
        "name": "airy-small",
        "version": "1.0.0",
        "quant": "q4_k_m",
        "runtime": {
            "engine": "llama.cpp",
            "version": "b4000",
            "path": engine,
            "backends": list(backends),
        },
        "assets": [{
            "path": "weight.gguf",
            "size": len(BODY),
            "sha256": digest or hashlib.sha256(BODY).hexdigest(),
        }],
    }


class Case(unittest.TestCase):
    """每个用例自备一份运行根，互不共享磁盘。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / "weight.gguf").write_bytes(BODY)
        self.exe = self.home / "llama-server"
        self.exe.write_bytes(b"\x7fELF")
        self.exe.chmod(0o755)
        (self.home / "ENGINE_VERSION").write_text("b4000\n", encoding="utf-8")

    def write(self, payload: dict) -> None:
        (self.home / model.MANIFEST_FILE).write_text(
            json.dumps(payload), encoding="utf-8")

    def test_ready(self) -> None:
        self.write(sample(str(self.exe)))
        got = engine.resolve(self.home, frozenset({model.Backend.CUDA}))
        self.assertIs(got.state, model.State.READY)
        self.assertIs(got.backend, model.Backend.CUDA)
        self.assertIsNone(got.reason)
        self.assertEqual(got.digest, hashlib.sha256(
            (self.home / model.MANIFEST_FILE).read_bytes()).hexdigest())

    def test_degraded(self) -> None:
        self.write(sample(str(self.exe)))
        got = engine.resolve(self.home, frozenset())
        self.assertIs(got.state, model.State.DEGRADED)
        self.assertIs(got.backend, model.Backend.CPU)
        self.assertIs(got.reason, model.Reason.NO_ACCEL)

    def test_status_persisted(self) -> None:
        self.write(sample(str(self.exe)))
        got = engine.resolve(self.home, frozenset({model.Backend.CUDA}))
        self.assertEqual(status.load(self.home), got)

    def test_manifest_missing(self) -> None:
        got = engine.resolve(self.home, frozenset())
        self.assertIs(got.state, model.State.FAILED)
        self.assertIs(got.reason, model.Reason.NO_MANIFEST)
        self.assertEqual(got.digest, "")
        self.assertEqual(status.load(self.home), got)

    def test_asset_corrupt(self) -> None:
        self.write(sample(str(self.exe), digest="0" * 64))
        got = engine.resolve(self.home, frozenset({model.Backend.CUDA}))
        self.assertIs(got.state, model.State.FAILED)
        self.assertIs(got.reason, model.Reason.DIGEST_MISMATCH)

    def test_no_cpu_backend(self) -> None:
        self.write(sample(str(self.exe), backends=("cuda",)))
        got = engine.resolve(self.home, frozenset())
        self.assertIs(got.state, model.State.FAILED)
        self.assertIs(got.reason, model.Reason.NO_RUNTIME)
        self.assertTrue(got.digest)

    def test_engine_missing(self) -> None:
        self.write(sample(str(self.home / "gone")))
        got = engine.resolve(self.home, frozenset({model.Backend.CUDA}))
        self.assertIs(got.state, model.State.FAILED)
        self.assertIs(got.reason, model.Reason.NO_RUNTIME)
        self.assertEqual(got.digest, "")

    def test_probe_default(self) -> None:
        self.write(sample(str(self.exe)))
        with mock.patch.object(engine.accel, "detect",
                               return_value=frozenset({model.Backend.CUDA})):
            got = engine.resolve(self.home)
        self.assertIs(got.state, model.State.READY)


if __name__ == "__main__":
    unittest.main()
