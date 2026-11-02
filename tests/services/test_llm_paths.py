# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 大模型运行根定位测试
#
# 断言路径推导只有契约常量一个来源、目录补建只增不改，以及环境变量
# 不参与定位——后者是审计属性，不是实现细节。

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyllm import model, paths  # noqa: E402


class BaseTest(unittest.TestCase):
    """默认根与相对根都必须按原样拼装，不做隐式解析。"""

    def test_default(self) -> None:
        self.assertEqual(paths.base(), Path(model.ROOT))

    def test_override(self) -> None:
        self.assertEqual(paths.base("/mnt/llm"), Path("/mnt/llm"))

    def test_path_object(self) -> None:
        self.assertEqual(paths.base(Path("/mnt/x")), Path("/mnt/x"))

    def test_relative(self) -> None:
        self.assertEqual(paths.base("rel"), Path("rel"))


class FileTest(unittest.TestCase):
    """三个落点相对运行根展开，文件名只能取自契约。"""

    def test_models(self) -> None:
        self.assertEqual(paths.models("/mnt/llm"),
                         Path("/mnt/llm") / model.MODELS)

    def test_manifest(self) -> None:
        self.assertEqual(paths.manifest("/mnt/llm"),
                         Path("/mnt/llm") / model.MANIFEST_FILE)

    def test_status(self) -> None:
        self.assertEqual(paths.status("/mnt/llm"),
                         Path("/mnt/llm") / model.STATUS_FILE)

    def test_defaults_end_under_root(self) -> None:
        home = Path(model.ROOT)
        for path in (paths.models(), paths.manifest(), paths.status()):
            self.assertTrue(path.is_relative_to(home), path)


class EnsureTest(unittest.TestCase):
    """补建幂等，且不改既有目录的权限。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "llm"

    def test_creates(self) -> None:
        self.assertEqual(paths.ensure(self.home), self.home)
        self.assertTrue(self.home.is_dir())
        self.assertTrue(paths.models(self.home).is_dir())

    def test_mode_private(self) -> None:
        """umask 可能再收紧，但运行时不得留下组/其他可写。"""
        paths.ensure(self.home)
        mode = self.home.stat().st_mode & 0o777
        self.assertEqual(mode & 0o700, 0o700)
        self.assertEqual(mode & 0o022, 0)

    def test_idempotent(self) -> None:
        paths.ensure(self.home)
        paths.models(self.home).chmod(0o755)
        paths.ensure(self.home)
        self.assertEqual(paths.models(self.home).stat().st_mode & 0o777,
                         0o755)

    def test_env_ignored(self) -> None:
        os.environ["AIRYMAX_LLM_ROOT"] = str(self.home)
        self.addCleanup(os.environ.pop, "AIRYMAX_LLM_ROOT", None)
        self.assertEqual(paths.base(), Path(model.ROOT))


if __name__ == "__main__":
    unittest.main()
