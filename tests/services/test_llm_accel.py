# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 推理加速后端选择测试
#
# 探测用夹具文件系统驱动，不依赖真机硬件；选择用例覆盖「交集为空」
# 「支持集少于可用集」「引擎无 CPU 后端」三条边界，后者必须硬失败。

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyllm import accel, model  # noqa: E402
from airyllm.failure import Failure  # noqa: E402


class DetectTest(unittest.TestCase):
    """探测只认设备节点与 ICD 清单，任一缺失即不算可用。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "dev").mkdir()

    def touch(self, name: str) -> None:
        (self.root / name).parent.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_bytes(b"")

    def test_empty(self) -> None:
        self.assertEqual(accel.detect(self.root), frozenset())

    def test_cuda(self) -> None:
        self.touch("dev/nvidia0")
        self.assertEqual(accel.detect(self.root), {model.Backend.CUDA})

    def test_rocm(self) -> None:
        self.touch("dev/kfd")
        self.assertEqual(accel.detect(self.root), {model.Backend.ROCM})

    def test_vulkan(self) -> None:
        (self.root / "dev/dri").mkdir()
        self.touch("usr/share/vulkan/icd.d/amd.json")
        self.assertEqual(accel.detect(self.root), {model.Backend.VULKAN})

    def test_vulkan_without_dri(self) -> None:
        self.touch("usr/share/vulkan/icd.d/amd.json")
        self.assertEqual(accel.detect(self.root), frozenset())

    def test_multiple(self) -> None:
        self.touch("dev/nvidia0")
        self.touch("dev/kfd")
        self.assertEqual(accel.detect(self.root),
                         {model.Backend.CUDA, model.Backend.ROCM})

    def test_str_root(self) -> None:
        self.touch("dev/nvidia0")
        self.assertEqual(accel.detect(str(self.root)), {model.Backend.CUDA})


class PickTest(unittest.TestCase):
    """选择按固定优先级，退 CPU 必须带归因，无 CPU 后端必须硬失败。"""

    def test_accel(self) -> None:
        self.assertEqual(
            accel.pick((model.Backend.CPU, model.Backend.CUDA),
                       frozenset({model.Backend.CUDA})),
            (model.Backend.CUDA, None),
        )

    def test_cpu_fallback(self) -> None:
        self.assertEqual(
            accel.pick((model.Backend.CPU, model.Backend.CUDA),
                       frozenset()),
            (model.Backend.CPU, model.Reason.NO_ACCEL),
        )

    def test_unsupported_hardware(self) -> None:
        self.assertEqual(
            accel.pick((model.Backend.CPU,), frozenset({model.Backend.CUDA})),
            (model.Backend.CPU, model.Reason.NO_ACCEL),
        )

    def test_priority(self) -> None:
        got = accel.pick((model.Backend.VULKAN, model.Backend.ROCM,
                          model.Backend.CPU),
                         frozenset({model.Backend.VULKAN,
                                    model.Backend.ROCM}))
        self.assertEqual(got, (model.Backend.ROCM, None))

    def test_no_cpu(self) -> None:
        with self.assertRaises(Failure) as caught:
            accel.pick((model.Backend.CUDA,), frozenset())
        self.assertIs(caught.exception.reason, model.Reason.NO_RUNTIME)


if __name__ == "__main__":
    unittest.main()
