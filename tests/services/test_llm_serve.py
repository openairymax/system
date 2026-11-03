# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 推理引擎启动参数测试
#
# 启动面是推理服务唯一不受清单约束的部分，故逐项钉住：权重只能有一个、
# 监听只能回环、层数只随后端变化。清单里出现两个 gguf 时不做挑选——
# 挑错权重不会报错，只会给出与清单不符的输出。

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyllm import model, serve  # noqa: E402
from airyllm.failure import Failure  # noqa: E402

SHA = "0" * 64


def manifest(*names: str) -> model.Manifest:
    return model.Manifest(
        schema=model.SCHEMA_VERSION,
        name="airy-small",
        version="1.0.0",
        quant="q4_k_m",
        runtime=model.Runtime(engine="llama.cpp", version="b4000",
                              path="/usr/libexec/airy-llm/llama-server"),
        assets=tuple(model.Asset(path=name, size=1, sha256=SHA)
                     for name in names),
    )


class ModelPathTest(unittest.TestCase):
    """权重从清单取，不从目录扫描取。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def test_single(self) -> None:
        got = serve.model_path(manifest("weights/airy-small.gguf"), self.home)
        self.assertEqual(got, (self.home / "weights/airy-small.gguf").resolve())

    def test_none(self) -> None:
        with self.assertRaises(Failure) as caught:
            serve.model_path(manifest("tokenizer.json"), self.home)
        self.assertIs(caught.exception.reason, model.Reason.NO_MODEL)

    def test_two(self) -> None:
        with self.assertRaises(Failure) as caught:
            serve.model_path(manifest("a.gguf", "b.gguf"), self.home)
        self.assertIs(caught.exception.reason, model.Reason.NO_MODEL)


class ArgvTest(unittest.TestCase):
    """命令行逐项比对：顺序与取值变了，systemd 日志里就认不出旧参数。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.entry = manifest("weight.gguf")

    def test_cpu(self) -> None:
        got = serve.argv(self.entry, model.Backend.CPU, self.home)
        self.assertEqual(got, [
            "/usr/libexec/airy-llm/llama-server",
            "--model", str((self.home / "weight.gguf").resolve()),
            "--alias", "airy-small",
            "--host", serve.HOST,
            "--port", str(serve.PORT),
            "--n-gpu-layers", "0",
        ])

    def test_accel(self) -> None:
        got = serve.argv(self.entry, model.Backend.CUDA, self.home)
        self.assertEqual(got[-1], str(serve.GPU_LAYERS))

    def test_host_loopback(self) -> None:
        self.assertEqual(serve.HOST, "127.0.0.1")


class NeedTest(unittest.TestCase):
    """常驻量只累加资产与地板，不给并发留猜测空间。"""

    def test_floor_only(self) -> None:
        self.assertEqual(serve.need(manifest()), serve.FLOOR)

    def test_assets(self) -> None:
        entry = model.Manifest(
            schema=model.SCHEMA_VERSION,
            name="airy-small",
            version="1.0.0",
            quant="q4_k_m",
            runtime=model.Runtime(engine="llama.cpp", version="b4000",
                                  path="/usr/libexec/airy-llm/llama-server"),
            assets=(model.Asset("weight.gguf", 5 << 30, SHA),),
        )
        self.assertEqual(serve.need(entry), (5 << 30) + serve.FLOOR)


if __name__ == "__main__":
    unittest.main()
