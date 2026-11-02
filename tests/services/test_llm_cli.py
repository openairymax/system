# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 模型命令行测试
#
# 四条对外契约各钉一条：install 的收敛与失败、status 的 JSON 字段与
# 退出码、verify 的强制重算、serve 只转交启动层。退出码是监控唯一能
# 直接消费的信号，故每条路径都同时断言它。

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyllm import cli, launch, model  # noqa: E402

BODY = b"weight-bytes"

ASSET = "weights/model.gguf"


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
            "backends": ["cpu"],
        },
        "assets": [{
            "path": ASSET,
            "size": len(BODY),
            "sha256": hashlib.sha256(BODY).hexdigest(),
        }],
    }


class Case(unittest.TestCase):
    """自备离线包与运行根；引擎只用到可执行位与版本标记。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.bundle = base / "bundle"
        self.home = base / "root"
        (self.bundle / "weights").mkdir(parents=True)
        (self.bundle / ASSET).write_bytes(BODY)
        self.engine = base / "engine" / "llama-server"
        self.engine.parent.mkdir()
        self.engine.write_bytes(b"\x7fELF")
        self.engine.chmod(0o755)
        (self.engine.parent / "ENGINE_VERSION").write_text(
            "b4000\n", encoding="utf-8")
        text = json.dumps(sample(str(self.engine)))
        (self.bundle / "manifest.json").write_text(text, encoding="utf-8")

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["--root", str(self.home), *argv])
        return code, out.getvalue(), err.getvalue()

    def install(self) -> None:
        code, _, err = self.run_cli("install", "--source", str(self.bundle))
        self.assertEqual(code, cli.EXIT_OK, err)

    def test_install_then_status_is_degraded(self) -> None:
        code, out, err = self.run_cli("install", "--source",
                                      str(self.bundle))
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertIn("installed: airy-small 1.0.0", out)
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, cli.EXIT_DEGRADED)
        self.assertIn("state=degraded", out)
        self.assertIn("reason=no-accel", out)

    def test_status_json_carries_manifest_digest(self) -> None:
        self.install()
        code, out, _ = self.run_cli("status", "--json")
        self.assertEqual(code, cli.EXIT_DEGRADED)
        payload = json.loads(out)
        self.assertEqual(set(payload), {"state", "backend", "reason",
                                        "digest"})
        self.assertEqual(payload["backend"], "cpu")
        self.assertEqual(payload["reason"], "no-accel")
        text = (self.home / "manifest.json").read_bytes()
        self.assertEqual(payload["digest"],
                         hashlib.sha256(text).hexdigest())

    def test_status_without_assets_fails(self) -> None:
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, cli.EXIT_FAIL)
        self.assertIn("state=failed", out)
        self.assertIn("reason=no-manifest", out)

    def test_verify_rechecks_a_tampered_asset(self) -> None:
        self.install()
        (self.home / ASSET).write_bytes(b"X" * len(BODY))
        code, out, _ = self.run_cli("verify")
        self.assertEqual(code, cli.EXIT_FAIL)
        self.assertIn("state=failed", out)
        self.assertIn("reason=digest-mismatch", out)

    def test_install_rejects_a_broken_bundle(self) -> None:
        (self.bundle / "manifest.json").unlink()
        code, _, err = self.run_cli("install", "--source",
                                    str(self.bundle))
        self.assertEqual(code, cli.EXIT_FAIL)
        self.assertIn("install:", err)

    def test_serve_hands_over_to_the_launcher(self) -> None:
        with mock.patch.object(launch, "run",
                               return_value=launch.EXIT_UNAVAILABLE) as run:
            code, _, _ = self.run_cli("serve")
        self.assertEqual(code, launch.EXIT_UNAVAILABLE)
        run.assert_called_once_with(str(self.home))


if __name__ == "__main__":
    unittest.main()
