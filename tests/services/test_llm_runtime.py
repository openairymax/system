# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 推理引擎检查测试
#
# 引擎是清单与磁盘之间的又一处可能失配：升级系统包会换掉引擎路径，清单
# 却还是旧的。每个用例只破坏一处，断言拒绝本身与归因，而不是错误措辞。

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyllm import model, runtime  # noqa: E402
from airyllm.failure import Failure  # noqa: E402


def manifest(path: str) -> model.Manifest:
    return model.Manifest(
        schema=model.SCHEMA_VERSION,
        name="airy-small",
        version="1.0.0",
        quant="q4_k_m",
        runtime=model.Runtime(engine="llama.cpp", version="b4000",
                              path=path),
    )


class CheckTest(unittest.TestCase):
    """可执行位按 mode 判，不按当前用户能否执行判。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.exe = self.dir / "llama-server"
        self.exe.write_bytes(b"\x7fELF")
        os.chmod(self.exe, 0o755)
        self.mark = self.dir / runtime.VERSION_FILE
        self.mark.write_text("b4000\n", encoding="utf-8")

    def reject(self, path: str) -> None:
        with self.assertRaises(Failure) as caught:
            runtime.check(manifest(path))
        self.assertIs(caught.exception.reason, model.Reason.NO_RUNTIME)

    def test_ok(self) -> None:
        runtime.check(manifest(str(self.exe)))

    def test_relative(self) -> None:
        self.reject("llama-server")

    def test_missing(self) -> None:
        self.reject(str(self.dir / "gone"))

    def test_directory(self) -> None:
        self.reject(str(self.dir))

    def test_no_exec(self) -> None:
        os.chmod(self.exe, stat.S_IRUSR | stat.S_IWUSR)
        self.reject(str(self.exe))

    def test_version_missing(self) -> None:
        self.mark.unlink()
        self.reject(str(self.exe))

    def test_version_mismatch(self) -> None:
        self.mark.write_text("b4100\n", encoding="utf-8")
        self.reject(str(self.exe))


if __name__ == "__main__":
    unittest.main()
