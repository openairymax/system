# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 推理服务启动测试
#
# 启动层的三个不变量：判定失败绝不 exec，判定降级必须先留痕再 exec，
# 准入拒绝绝不 exec 且给出缺口。exec 本身不可在测试进程里真跑，故只钉住
# 传给它的参数与各路径的退出码。准入一律注入判定结果，不读宿主机内存——
# 判据来自现场 /proc/meminfo，用它做断言会使用例随开发者机器的负载漂移。

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

from airyllm import accel, launch, model, serve  # noqa: E402
from airymax_rt import admit  # noqa: E402

BODY = b"weight-bytes"


def verdict(state: str) -> admit.Verdict:
    """按状态造一份判定，缺口固定 1 GiB，便于逐字比对日志。"""
    short = 1 << 30
    free = 8 << 30 if state == admit.FIT else 7 << 30
    need = free + (0 if state == admit.FIT else short)
    return admit.Verdict(state, need, free, 16 << 30, free, 0, 0)


def allow(_: int) -> admit.Verdict:
    return verdict(admit.FIT)


MISSING = (
    accel.Observation(model.Backend.CUDA, False,
                      ("missing:dev/nvidiactl", "missing:dev/nvidia0")),
    accel.Observation(model.Backend.CPU, True, ("builtin",)),
)

READY = (
    accel.Observation(model.Backend.CUDA, True, ("ok:dev/nvidiactl",
                                                 "ok:dev/nvidia0")),
    accel.Observation(model.Backend.CPU, True, ("builtin",)),
)


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
        got, argv, need = launch.plan(self.home,
                                      frozenset({model.Backend.CUDA}))
        self.assertIs(got.state, model.State.READY)
        self.assertEqual(argv, [
            str(self.exe),
            "--model", str((self.home / "weight.gguf").resolve()),
            "--alias", "airy-small",
            "--host", serve.HOST,
            "--port", str(serve.PORT),
            "--n-gpu-layers", str(serve.GPU_LAYERS),
        ])
        self.assertEqual(need, len(BODY) + serve.FLOOR)

    def test_failed(self) -> None:
        got, argv, need = launch.plan(self.home, frozenset())
        self.assertIs(got.state, model.State.FAILED)
        self.assertEqual(argv, [])
        self.assertEqual(need, 0)

    def test_degraded(self) -> None:
        self.write(sample(str(self.exe)))
        got, argv, _ = launch.plan(self.home, frozenset())
        self.assertIs(got.state, model.State.DEGRADED)
        self.assertEqual(argv[-1], "0")


class RunTest(Base):
    """判定失败返回配置错误码，降级照常启动。"""

    def test_unavailable(self) -> None:
        self.assertEqual(launch.run(self.home, frozenset(), allow),
                         launch.EXIT_UNAVAILABLE)

    def test_exec_argv(self) -> None:
        self.write(sample(str(self.exe)))
        with mock.patch.object(launch.os, "execv") as called:
            code = launch.run(self.home, frozenset({model.Backend.CUDA}),
                              allow)
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
                code = launch.run(self.home,
                                  frozenset({model.Backend.CUDA}), allow)
        self.assertEqual(code, launch.EXIT_UNAVAILABLE)
        self.assertIn("exec: denied", err.getvalue())

    def test_degraded_notice(self) -> None:
        self.write(sample(str(self.exe)))
        with mock.patch.object(launch.os, "execv"):
            err = io.StringIO()
            with redirect_stderr(err):
                launch.run(self.home, frozenset(), allow)
        self.assertIn("degraded:no-accel", err.getvalue())

    def test_decline_evidence(self) -> None:
        self.write(sample(str(self.exe)))
        with mock.patch.object(launch.accel, "probe", return_value=MISSING), \
                mock.patch.object(launch.os, "execv"):
            err = io.StringIO()
            with redirect_stderr(err):
                launch.run(self.home, check=allow)
        self.assertIn("probe: cuda missing:dev/nvidiactl "
                      "missing:dev/nvidia0", err.getvalue())

    def test_probe_once(self) -> None:
        self.write(sample(str(self.exe)))
        with mock.patch.object(launch.accel, "probe",
                               return_value=READY) as seen, \
                mock.patch.object(launch.os, "execv"):
            launch.run(self.home, check=allow)
        self.assertEqual(seen.call_count, 1)

    def test_ready_silent(self) -> None:
        self.write(sample(str(self.exe)))
        with mock.patch.object(launch.accel, "probe", return_value=READY), \
                mock.patch.object(launch.os, "execv"):
            err = io.StringIO()
            with redirect_stderr(err):
                launch.run(self.home, check=allow)
        self.assertEqual(err.getvalue(), "")


class AdmitTest(Base):
    """准入拒绝时不 exec，把缺口写进日志；判定不可得时放行。"""

    def test_refuse(self) -> None:
        self.write(sample(str(self.exe)))
        with mock.patch.object(launch.os, "execv") as called:
            err = io.StringIO()
            with redirect_stderr(err):
                code = launch.run(self.home,
                                  frozenset({model.Backend.CUDA}),
                                  lambda need: verdict(admit.REFUSE))
        self.assertEqual(code, launch.EXIT_REFUSED)
        self.assertFalse(called.called)
        self.assertIn("admit: refuse need=8589934592 free=7516192768 "
                      "short=1073741824", err.getvalue())

    def test_need_from_manifest(self) -> None:
        """判定拿到的需求量与清单一致，不是零点或固定值。"""
        self.write(sample(str(self.exe)))
        seen: list[int] = []
        with mock.patch.object(launch.os, "execv"):
            launch.run(self.home, frozenset({model.Backend.CUDA}),
                       lambda need: (seen.append(need), verdict(admit.FIT))[1])
        self.assertEqual(seen, [len(BODY) + serve.FLOOR])

    def test_unknown_allows(self) -> None:
        self.write(sample(str(self.exe)))
        with mock.patch.object(launch.os, "execv") as called:
            err = io.StringIO()
            with redirect_stderr(err):
                code = launch.run(self.home,
                                  frozenset({model.Backend.CUDA}),
                                  lambda need: verdict(admit.UNKNOWN))
        self.assertEqual(code, launch.EXIT_UNAVAILABLE)
        self.assertTrue(called.called)
        self.assertIn("admit: unknown", err.getvalue())

    def test_refuse_before_degrade(self) -> None:
        """拒绝优先于降级提示：装不下时不该先报一句降级再退出。"""
        self.write(sample(str(self.exe)))
        with mock.patch.object(launch.os, "execv"):
            err = io.StringIO()
            with redirect_stderr(err):
                launch.run(self.home, frozenset(),
                           lambda need: verdict(admit.REFUSE))
        self.assertNotIn("degraded", err.getvalue())


if __name__ == "__main__":
    unittest.main()
