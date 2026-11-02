# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 模型清单解析测试
#
# 清单是外部交付物，因此每个用例都从「合法清单」出发只破坏一处，
# 断言的是拒绝本身与归因 reason，而不是错误措辞。

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyllm import manifest, model  # noqa: E402
from airyllm.failure import Failure  # noqa: E402

DIGEST = "0" * 64


def sample() -> dict:
    return {
        "schema": model.SCHEMA_VERSION,
        "name": "airy-small",
        "version": "1.0.0",
        "quant": "q4_k_m",
        "runtime": {
            "engine": "llama.cpp",
            "version": "b4000",
            "path": "/usr/libexec/airy-llm/llama-server",
            "backends": ["cpu"],
        },
        "assets": [
            {"path": "weight.gguf", "size": 1024, "sha256": DIGEST},
        ],
    }


class ParseTest(unittest.TestCase):
    """合法清单必须无损落到契约对象。"""

    def test_valid(self) -> None:
        got = manifest.parse(json.dumps(sample()))
        self.assertIsInstance(got, model.Manifest)
        self.assertEqual(got.name, "airy-small")
        self.assertIs(got.runtime.backends[0], model.Backend.CPU)
        self.assertEqual(got.assets[0].path, "weight.gguf")
        self.assertEqual(got.assets[0].sha256, DIGEST)

    def test_multi_backend(self) -> None:
        obj = sample()
        obj["runtime"]["backends"] = ["cuda", "cpu"]
        got = manifest.parse(json.dumps(obj))
        self.assertEqual(
            [backend.value for backend in got.runtime.backends],
            ["cuda", "cpu"],
        )

    def test_nested_asset(self) -> None:
        obj = sample()
        obj["assets"][0]["path"] = "sub/dir/weight.gguf"
        self.assertEqual(
            manifest.parse(json.dumps(obj)).assets[0].path,
            "sub/dir/weight.gguf",
        )


class RejectTest(unittest.TestCase):
    """每类偏离都要在解析期拒绝，并带上可落盘的归因。"""

    def reject(self, reason: model.Reason, mutate) -> None:
        obj = copy.deepcopy(sample())
        mutate(obj)
        with self.assertRaises(Failure) as caught:
            manifest.parse(json.dumps(obj))
        self.assertIs(caught.exception.reason, reason)

    def test_not_json(self) -> None:
        with self.assertRaises(Failure) as caught:
            manifest.parse("{")
        self.assertIs(caught.exception.reason, model.Reason.NO_MANIFEST)

    def test_not_object(self) -> None:
        with self.assertRaises(Failure):
            manifest.parse("[]")

    def test_schema(self) -> None:
        def mutate(obj: dict) -> None:
            obj["schema"] = model.SCHEMA_VERSION + 1

        self.reject(model.Reason.NO_MANIFEST, mutate)

    def test_unknown_field(self) -> None:
        def mutate(obj: dict) -> None:
            obj["extra"] = 1

        self.reject(model.Reason.NO_MANIFEST, mutate)

    def test_missing_field(self) -> None:
        def mutate(obj: dict) -> None:
            del obj["quant"]

        self.reject(model.Reason.NO_MANIFEST, mutate)

    def test_empty_name(self) -> None:
        def mutate(obj: dict) -> None:
            obj["name"] = ""

        self.reject(model.Reason.NO_MANIFEST, mutate)

    def test_runtime_not_object(self) -> None:
        def mutate(obj: dict) -> None:
            obj["runtime"] = []

        self.reject(model.Reason.NO_RUNTIME, mutate)

    def test_backends_empty(self) -> None:
        def mutate(obj: dict) -> None:
            obj["runtime"]["backends"] = []

        self.reject(model.Reason.NO_RUNTIME, mutate)

    def test_backend_unknown(self) -> None:
        def mutate(obj: dict) -> None:
            obj["runtime"]["backends"] = ["tpu"]

        self.reject(model.Reason.NO_ACCEL, mutate)

    def test_assets_empty(self) -> None:
        def mutate(obj: dict) -> None:
            obj["assets"] = []

        self.reject(model.Reason.NO_MODEL, mutate)

    def test_asset_unknown_field(self) -> None:
        def mutate(obj: dict) -> None:
            obj["assets"][0]["mode"] = "0644"

        self.reject(model.Reason.ASSET_CORRUPT, mutate)

    def test_size_negative(self) -> None:
        def mutate(obj: dict) -> None:
            obj["assets"][0]["size"] = -1

        self.reject(model.Reason.ASSET_CORRUPT, mutate)

    def test_size_bool(self) -> None:
        def mutate(obj: dict) -> None:
            obj["assets"][0]["size"] = True

        self.reject(model.Reason.ASSET_CORRUPT, mutate)

    def test_digest_upper(self) -> None:
        def mutate(obj: dict) -> None:
            obj["assets"][0]["sha256"] = ("ab" * 32).upper()

        self.reject(model.Reason.DIGEST_MISMATCH, mutate)

    def test_digest_short(self) -> None:
        def mutate(obj: dict) -> None:
            obj["assets"][0]["sha256"] = DIGEST[:-1]

        self.reject(model.Reason.DIGEST_MISMATCH, mutate)

    def test_path_absolute(self) -> None:
        def mutate(obj: dict) -> None:
            obj["assets"][0]["path"] = "/etc/shadow"

        self.reject(model.Reason.ASSET_CORRUPT, mutate)

    def test_path_parent(self) -> None:
        def mutate(obj: dict) -> None:
            obj["assets"][0]["path"] = "models/../../etc/shadow"

        self.reject(model.Reason.ASSET_CORRUPT, mutate)

    def test_path_empty_segment(self) -> None:
        def mutate(obj: dict) -> None:
            obj["assets"][0]["path"] = "models//weight.gguf"

        self.reject(model.Reason.ASSET_CORRUPT, mutate)

    def test_path_duplicate(self) -> None:
        def mutate(obj: dict) -> None:
            obj["assets"].append(dict(obj["assets"][0]))

        self.reject(model.Reason.ASSET_CORRUPT, mutate)


class LoadTest(unittest.TestCase):
    """文件形态入口与文本入口同语义，读不到不抛 OSError。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_roundtrip(self) -> None:
        path = self.dir / model.MANIFEST_FILE
        path.write_text(json.dumps(sample()), encoding="utf-8")
        self.assertEqual(manifest.load(path).name, "airy-small")

    def test_missing(self) -> None:
        with self.assertRaises(Failure) as caught:
            manifest.load(self.dir / "nope.json")
        self.assertIs(caught.exception.reason, model.Reason.NO_MANIFEST)


if __name__ == "__main__":
    unittest.main()
