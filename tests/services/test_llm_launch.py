# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 推理服务启动测试
#
# 启动层的两个不变量：判定失败绝不 exec，判定降级必须先留痕再 exec。
# exec 本身不可在测试进程里真跑，故只钉住传给它的参数与失败时的退出码。

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyllm import launch, model, serve  # noqa: E402

BODY = b"weight-bytes"


def sample(engine: str) -> dict:
    return {
        "schema": model.SCHEMA_VERSION,
        "name": "airy-small",
        "version": "1.0.0",
        "quant": "q4_k_m",
        "runtime": {
            "engine": "llama.cpp",
            "version": "b4000",
            "path": engine,
            "backends": ["cuda", "cpu"],
        },
        "assets": [{
            "path": "weight.gguf",
            "size": len(BODY),
            "sha256": hashlib.sha256(BODY).hexdigest(),
        }],
    }


class Base(unittest.TestCase):
    """自备运行根：权重、引擎与版本标记齐备。"""

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


class PlanTest(Base):
    """参数与判定出自同一次读取。"""

    def test_ready(self) -> None:
        self.write(sample(str(self.exe)))
        got, argv = launch.plan(self.home, frozenset({model.Backend.CUDA}))
        self.assertIs(got.state, model.State.READY)
        self.assertEqual(argv, [
            str(self.exe),
            "--model", str((self.home / "weight.gguf").resolve()),
            "--alias", "airy-small",
            "--host", serve.HOST,
            "--port", str(serve.PORT),
            "--n-gpu-layers", str(serve.GPU_LAYERS),
        ])

    def test_failed(self) -> None:
        got, argv = launch.plan(self.home, frozenset())
        self.assertIs(got.state, model.State.FAILED)
        self.assertEqual(argv, [])

    def test_degraded(self) -> None:
        self.write(sample(str(self.exe)))
        got, argv = launch.plan(self.home, frozenset())
        self.assertIs(got.state, model.State.DEGRADED)
        self.assertEqual(argv[-1], "0")


class RunTest(Base):
    """判定失败返回配置错误码，降级照常启动。"""

    def test_unavailable(self) -> None:
        self.assertEqual(launch.run(self.home, frozenset()),
                         launch.EXIT_UNAVAILABLE)

    def test_exec_argv(self) -> None:
        self.write(sample(str(self.exe)))
        with mock.patch.object(launch.os, "execv") as called:
            code = launch.run(self.home, frozenset({model.Backend.CUDA}))
        self.assertEqual(code, launch.EXIT_UNAVAILABLE)
        argv = called.call_args.args[1]
        self.assertEqual(called.call_args.args[0], argv[0])
        self.assertIn(str(self.exe), argv)

    def test_exec_error(self) -> None:
        self.write(sample(str(self.exe)))
        with mock.patch.object(launch.os, "execv",
                               side_effect=OSError("denied")):
            err = io.StringIO()
            with redirect_stderr(err):
                code = launch.run(self.home, frozenset({model.Backend.CUDA}))
        self.assertEqual(code, launch.EXIT_UNAVAILABLE)
        self.assertIn("exec: denied", err.getvalue())

    def test_degraded_notice(self) -> None:
        self.write(sample(str(self.exe)))
        with mock.patch.object(launch.os, "execv"):
            err = io.StringIO()
            with redirect_stderr(err):
                launch.run(self.home, frozenset())
        self.assertIn("degraded:no-accel", err.getvalue())


if __name__ == "__main__":
    unittest.main()
