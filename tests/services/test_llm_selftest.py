#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 模型离线自检测试
#
# 自检的价值全在「失败可归因」：任何一项不过，调用方都能从名字与原因
# 直接判断该修什么。因此每条用例都断言首个失败项的名字与 reason token，
# 而不是只看退出码——退出码只有三档，修法有六种。
#
# 被阻塞的项必须留名：少一行输出，运维就会以为那一层没检查过，而实际上
# 它只是没机会执行。

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyllm import accel, cli, model, report, selftest  # noqa: E402
from airyllm.selftest import steps  # noqa: E402

BODY = b"weight-bytes"

BANNER = "version: b4000"

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
            "backends": ["cuda", "cpu"],
        },
        "assets": [{
            "path": ASSET,
            "size": len(BODY),
            "sha256": hashlib.sha256(BODY).hexdigest(),
        }],
    }


class Case(unittest.TestCase):
    """自备运行根；引擎是可执行脚本，冒烟真的会被跑一次。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home = base / "root"
        (self.home / "weights").mkdir(parents=True)
        (self.home / ASSET).write_bytes(BODY)
        self.engine = base / "engine" / "llama-server"
        self.engine.parent.mkdir()
        self.engine.write_text(f"#!/bin/sh\necho '{BANNER}'\n",
                               encoding="utf-8")
        self.engine.chmod(0o755)
        (self.engine.parent / "ENGINE_VERSION").write_text(
            "b4000\n", encoding="utf-8")
        self.write(sample(str(self.engine)))

    def write(self, payload: dict) -> None:
        (self.home / model.MANIFEST_FILE).write_text(
            json.dumps(payload), encoding="utf-8")

    def rows(self, seen: frozenset = frozenset()) -> selftest.Report:
        return selftest.run(self.home, seen)

    def bad(self, got: selftest.Report) -> list[str]:
        return [row.name for row in got.rows if not row.ok]


class VerdictTest(Case):
    """就绪与降级都要求八项全过；差别只在后端与原因。"""

    def test_ready(self) -> None:
        got = self.rows(frozenset({model.Backend.CUDA}))
        self.assertIs(got.status.state, model.State.READY)
        self.assertIs(got.status.backend, model.Backend.CUDA)
        self.assertEqual([row.name for row in got.rows], list(selftest.CHECKS))

    def test_degraded(self) -> None:
        got = self.rows()
        self.assertIs(got.status.state, model.State.DEGRADED)
        self.assertIs(got.status.reason, model.Reason.NO_ACCEL)
        self.assertEqual(self.bad(got), [])
        self.assertTrue((self.home / model.STATUS_FILE).is_file())

    def test_digest(self) -> None:
        digest = hashlib.sha256(
            (self.home / model.MANIFEST_FILE).read_bytes()).hexdigest()
        self.assertEqual(self.rows().status.digest, digest)


class FailureTest(Case):
    """每条失败路径只该有一个责任项，其余后续项按名标为 blocked。"""

    def test_no_manifest(self) -> None:
        (self.home / model.MANIFEST_FILE).unlink()
        got = self.rows()
        self.assertIs(got.status.state, model.State.FAILED)
        self.assertIs(got.status.reason, model.Reason.NO_MANIFEST)
        self.assertEqual(self.bad(got), list(selftest.CHECKS[1:7]))
        self.assertEqual(got.rows[2].detail, "blocked by manifest")

    def test_tamper(self) -> None:
        (self.home / ASSET).write_bytes(b"x" * len(BODY))
        got = self.rows()
        self.assertIs(got.status.reason, model.Reason.DIGEST_MISMATCH)
        self.assertEqual(self.bad(got)[0], "assets")

    def test_truncate(self) -> None:
        (self.home / ASSET).write_bytes(BODY[:3])
        self.assertIs(self.rows().status.reason, model.Reason.ASSET_CORRUPT)

    def test_no_engine(self) -> None:
        self.write(sample(str(self.home / "absent")))
        got = self.rows()
        self.assertIs(got.status.reason, model.Reason.NO_RUNTIME)
        self.assertEqual(self.bad(got)[0], "engine")

    def test_smoke_fails(self) -> None:
        self.engine.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
        got = self.rows()
        self.assertIs(got.status.reason, model.Reason.NO_RUNTIME)
        self.assertEqual(self.bad(got)[0], "smoke")

    def test_smoke_timeout(self) -> None:
        with mock.patch.object(steps.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("-", 1)):
            got = self.rows()
        self.assertIs(got.status.reason, model.Reason.NO_RUNTIME)
        self.assertEqual(self.bad(got)[0], "smoke")

    def test_store_fails(self) -> None:
        with mock.patch.object(steps.status, "store", side_effect=OSError):
            got = self.rows()
        self.assertIs(got.status.reason, model.Reason.STATE_UNWRITABLE)
        self.assertEqual(self.bad(got)[-1], "status")

    def test_readback_fails(self) -> None:
        with mock.patch.object(steps.status, "load", return_value=None):
            got = self.rows()
        self.assertIs(got.status.reason, model.Reason.STATE_UNWRITABLE)


class RenderTest(Case):
    """行式与 JSON 出自同一份报告，字段名不得分头维护。"""

    def test_fields(self) -> None:
        obj = report.selftest_fields(self.rows())
        self.assertEqual(sorted(obj),
                         ["backend", "checks", "digest", "reason", "state"])
        self.assertEqual([row["name"] for row in obj["checks"]],
                         list(selftest.CHECKS))
        self.assertEqual(sorted(obj["checks"][0]),
                         ["detail", "name", "ok", "reason"])

    def test_check_line(self) -> None:
        got = self.rows()
        self.assertTrue(report.check_line(got.rows[0]).startswith("root=ok "))
        (self.home / model.MANIFEST_FILE).unlink()
        row = self.rows().rows[1]
        self.assertTrue(report.check_line(row)
                        .startswith("manifest=fail reason=no-manifest"))


class CliTest(Case):
    """退出码与 status/verify 同源；探测结果由用例给定，避免机器相关。"""

    def run_cli(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.object(accel, "detect", return_value=frozenset()), \
                redirect_stdout(out):
            code = cli.main(["--root", str(self.home), *argv])
        return code, out.getvalue()

    def test_json(self) -> None:
        code, text = self.run_cli("selftest", "--json")
        self.assertEqual(code, cli.EXIT_DEGRADED)
        obj = json.loads(text)
        self.assertEqual(obj["state"], "degraded")
        self.assertEqual(len(obj["checks"]), len(selftest.CHECKS))

    def test_lines(self) -> None:
        code, text = self.run_cli("selftest")
        lines = text.strip().splitlines()
        self.assertEqual(code, cli.EXIT_DEGRADED)
        self.assertEqual(lines[-1], report.line(self.rows().status))
        self.assertEqual(len(lines), len(selftest.CHECKS) + 1)

    def test_failed_code(self) -> None:
        (self.home / model.MANIFEST_FILE).unlink()
        code, _ = self.run_cli("selftest", "--json")
        self.assertEqual(code, cli.EXIT_FAIL)


if __name__ == "__main__":
    unittest.main()
