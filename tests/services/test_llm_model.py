# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 大模型运行环境契约测试
#
# 校验 model 的类型契约：枚举 token 与状态文件/探测输出逐字一致、
# 路径常量合法、字段白名单与数据类同步、数据类不可变。清单读取、
# 摘要校验与后端探测不在本文件覆盖。

from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyllm import model  # noqa: E402


class TokenTest(unittest.TestCase):
    """枚举取值即协议：改名会破坏状态文件与探测输出的对照关系。"""

    def test_backend(self) -> None:
        self.assertEqual(
            [kind.value for kind in model.Backend],
            ["cpu", "cuda", "rocm", "vulkan"],
        )

    def test_state(self) -> None:
        self.assertEqual(
            [state.value for state in model.State],
            ["ready", "degraded", "failed"],
        )

    def test_reason(self) -> None:
        self.assertEqual(
            {reason.value for reason in model.Reason},
            {
                "no-accel",
                "no-runtime",
                "no-model",
                "no-manifest",
                "digest-mismatch",
                "asset-corrupt",
            },
        )

    def test_no_alias(self) -> None:
        for cls in (model.Backend, model.State, model.Reason):
            self.assertEqual(len(cls), len(cls.__members__), cls.__name__)


class ConstTest(unittest.TestCase):
    """运行根与文件名是安装器、服务与门禁共用的路径契约。"""

    def test_schema(self) -> None:
        self.assertEqual(model.SCHEMA_VERSION, 1)

    def test_root(self) -> None:
        self.assertTrue(Path(model.ROOT).is_absolute())
        self.assertTrue(model.ROOT.startswith("/var/lib/"))

    def test_names(self) -> None:
        self.assertEqual(model.MODELS, "models")
        self.assertEqual(model.MANIFEST_FILE, "manifest.json")
        self.assertEqual(model.STATUS_FILE, "status.json")
        self.assertEqual(model.REASON_SEP, ":")
        self.assertEqual(model.ROOT_ENV, "AIRYMAX_LLM_ROOT")

    def test_fields(self) -> None:
        self.assertEqual(
            model.ASSET_FIELDS, frozenset({"path", "size", "sha256"}))
        self.assertEqual(
            model.RUNTIME_FIELDS,
            frozenset({"engine", "version", "path", "backends"}),
        )
        self.assertEqual(
            model.MANIFEST_FIELDS,
            frozenset({
                "schema",
                "name",
                "version",
                "quant",
                "runtime",
                "assets",
            }),
        )

    def test_export(self) -> None:
        for name in model.__all__:
            self.assertTrue(hasattr(model, name), name)


class DefaultTest(unittest.TestCase):
    """默认值必须能在不补任何字段的情况下组合出合法对象。"""

    def test_asset(self) -> None:
        asset = model.Asset(path="models/a.gguf", size=7, sha256="0" * 64)
        self.assertEqual(asset.size, 7)

    def test_runtime(self) -> None:
        runtime = model.Runtime(
            engine="llama.cpp", version="b6000", path="/usr/bin/llama-cli")
        self.assertEqual(runtime.backends, (model.Backend.CPU,))

    def test_manifest(self) -> None:
        runtime = model.Runtime(
            engine="llama.cpp", version="b6000", path="/usr/bin/llama-cli")
        manifest = model.Manifest(
            schema=model.SCHEMA_VERSION,
            name="airymax-mini",
            version="1",
            quant="q4_k_m",
            runtime=runtime,
        )
        self.assertEqual(manifest.assets, ())

    def test_status(self) -> None:
        status = model.Status(
            state=model.State.READY, backend=model.Backend.CPU)
        self.assertIsNone(status.reason)
        self.assertEqual(status.digest, "")


class FreezeTest(unittest.TestCase):
    """契约对象只读：运行层不得就地改写判定结果。"""

    def test_status(self) -> None:
        status = model.Status(
            state=model.State.READY, backend=model.Backend.CPU)
        with self.assertRaises(FrozenInstanceError):
            status.state = model.State.FAILED

    def test_manifest(self) -> None:
        runtime = model.Runtime(
            engine="llama.cpp", version="b6000", path="/usr/bin/llama-cli")
        manifest = model.Manifest(
            schema=model.SCHEMA_VERSION,
            name="airymax-mini",
            version="1",
            quant="q4_k_m",
            runtime=runtime,
        )
        with self.assertRaises(FrozenInstanceError):
            manifest.name = "other"


if __name__ == "__main__":
    unittest.main()
