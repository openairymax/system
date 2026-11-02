# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 推理加速后端探测与选择测试
#
# 探测用例由夹具文件系统驱动，不依赖真机硬件：设备节点用普通文件代替，
# 判据是当前身份可读写，故既能覆盖「节点不存在」，也能覆盖「节点在但打
# 不开」。证据 token 是降级日志的对外契约，逐条断言其前缀，避免实现改动
# 后日志悄悄失去排障价值。
#
# 选择用例覆盖交集为空、支持集少于可用集、引擎无 CPU 后端三条边界，后者
# 必须硬失败——把没有后端的服务拉起来，只会让 systemd 报一串引擎错误。

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyllm import accel, model  # noqa: E402
from airyllm.failure import Failure  # noqa: E402

AMD = {"ICD": {"api_version": "1.3.0",
               "library_path": "libvulkan_radeon.so"}}


class Base(unittest.TestCase):
    """自备一个假的系统根，设备与 ICD 清单都落在这里。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "dev").mkdir()

    def touch(self, name: str, mode: int = 0o644) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        path.chmod(mode)

    def icd(self, name: str, body: object = AMD) -> None:
        path = self.root / "usr/share/vulkan/icd.d" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body), encoding="utf-8")


class DetectTest(Base):
    """探测只认可用节点与可解析的 ICD，任一缺失即不算可用。"""

    def test_empty(self) -> None:
        self.assertEqual(accel.detect(self.root), frozenset())

    def test_cuda(self) -> None:
        self.touch("dev/nvidiactl")
        self.touch("dev/nvidia0")
        self.assertEqual(accel.detect(self.root), {model.Backend.CUDA})

    def test_cuda_needs_control_node(self) -> None:
        self.touch("dev/nvidia0")
        self.assertEqual(accel.detect(self.root), frozenset())

    def test_rocm(self) -> None:
        self.touch("dev/kfd")
        self.touch("dev/dri/renderD128")
        self.assertEqual(accel.detect(self.root), {model.Backend.ROCM})

    def test_rocm_needs_render_node(self) -> None:
        self.touch("dev/kfd")
        self.assertEqual(accel.detect(self.root), frozenset())

    def test_vulkan(self) -> None:
        (self.root / "dev/dri").mkdir()
        self.icd("radeon.json")
        self.assertEqual(accel.detect(self.root), {model.Backend.VULKAN})

    def test_vulkan_without_dri(self) -> None:
        self.icd("radeon.json")
        self.assertEqual(accel.detect(self.root), frozenset())

    def test_vulkan_stale_library(self) -> None:
        (self.root / "dev/dri").mkdir()
        self.icd("radeon.json", {"ICD": {"library_path": "/usr/lib/x.so"}})
        self.assertEqual(accel.detect(self.root), frozenset())

    def test_vulkan_bare_soname(self) -> None:
        (self.root / "dev/dri").mkdir()
        self.icd("nvidia.json",
                 {"ICD": {"library_path": "libGLX_nvidia.so.0"}})
        self.assertEqual(accel.detect(self.root), {model.Backend.VULKAN})

    def test_multiple(self) -> None:
        self.touch("dev/nvidiactl")
        self.touch("dev/nvidia0")
        self.touch("dev/kfd")
        self.touch("dev/dri/renderD128")
        self.assertEqual(accel.detect(self.root),
                         {model.Backend.CUDA, model.Backend.ROCM})

    def test_str_root(self) -> None:
        self.touch("dev/nvidiactl")
        self.touch("dev/nvidia0")
        self.assertEqual(accel.detect(str(self.root)), {model.Backend.CUDA})

    @unittest.skipIf(os.geteuid() == 0, "root 无视文件权限")
    def test_denied_node_is_absent(self) -> None:
        self.touch("dev/nvidiactl")
        self.touch("dev/nvidia0", 0o000)
        self.assertEqual(accel.detect(self.root), frozenset())


class ProbeTest(Base):
    """观察固定按优先级排列，且每条都带可直接打印的依据。"""

    def test_order_and_cpu(self) -> None:
        rows = accel.probe(self.root)
        self.assertEqual(tuple(row.backend for row in rows), accel.ORDER)
        self.assertEqual(rows[-1].evidence, ("builtin",))
        self.assertTrue(rows[-1].present)

    def test_cuda_evidence(self) -> None:
        rows = accel.probe(self.root)
        self.assertEqual(rows[0].evidence,
                         ("missing:dev/nvidiactl", "missing:dev/nvidia0"))
        self.assertFalse(rows[0].present)

    def test_rocm_evidence(self) -> None:
        self.touch("dev/kfd")
        self.touch("dev/dri/renderD128")
        rows = accel.probe(self.root)
        self.assertEqual(rows[1].evidence,
                         ("ok:dev/kfd", "ok:dev/dri/renderD128"))
        self.assertTrue(rows[1].present)

    def test_vulkan_evidence(self) -> None:
        (self.root / "dev/dri").mkdir()
        self.icd("radeon.json")
        rows = accel.probe(self.root)
        self.assertEqual(rows[2].evidence,
                         ("ok:dev/dri",
                          "unverified:usr/share/vulkan/icd.d/radeon.json"))
        self.assertTrue(rows[2].present)

    def test_vulkan_unparsable_icd(self) -> None:
        (self.root / "dev/dri").mkdir()
        path = self.root / "usr/share/vulkan/icd.d/broken.json"
        path.parent.mkdir(parents=True)
        path.write_text("", encoding="utf-8")
        rows = accel.probe(self.root)
        self.assertFalse(rows[2].present)
        self.assertTrue(rows[2].evidence[1].startswith("stale:"))

    def test_present_excludes_cpu(self) -> None:
        self.assertEqual(accel.present(accel.probe(self.root)), frozenset())


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
            accel.pick((model.Backend.CPU, model.Backend.CUDA), frozenset()),
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
