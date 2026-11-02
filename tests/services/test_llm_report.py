# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 模型运行状态表示测试
#
# token 是对外契约：自检、CLI 与单元条件都按它匹配，故三态逐一钉死；
# 行式输出供人阅读，只断言字段的有无，不锁定字段顺序以外的措辞。

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyllm import model, report  # noqa: E402

DIGEST = "ab" * 32


class FieldsTest(unittest.TestCase):
    """``status --json`` 的字段集固定，缺省用 ``None`` 与空串表达。"""

    def test_ready(self) -> None:
        entry = model.Status(state=model.State.READY,
                             backend=model.Backend.CUDA, digest=DIGEST)
        self.assertEqual(report.fields(entry),
                         {"state": "ready", "backend": "cuda",
                          "reason": None, "digest": DIGEST})

    def test_degraded(self) -> None:
        entry = model.Status(state=model.State.DEGRADED,
                             backend=model.Backend.CPU,
                             reason=model.Reason.NO_ACCEL, digest=DIGEST)
        self.assertEqual(report.fields(entry),
                         {"state": "degraded", "backend": "cpu",
                          "reason": "no-accel", "digest": DIGEST})

    def test_json(self) -> None:
        """映射必须可直出 JSON，原因取枚举 token 而非自由文本。"""
        entry = model.Status(state=model.State.FAILED,
                             backend=model.Backend.CPU,
                             reason=model.Reason.NO_MANIFEST)
        obj = json.loads(json.dumps(report.fields(entry)))
        self.assertEqual(obj["reason"], "no-manifest")
        self.assertEqual(obj["digest"], "")


class TokenTest(unittest.TestCase):
    """token 只由状态与原因构成，摘要与后端不参与。"""

    def test_ready(self) -> None:
        entry = model.Status(state=model.State.READY,
                             backend=model.Backend.CUDA, digest=DIGEST)
        self.assertEqual(report.token(entry), "ready")

    def test_degraded(self) -> None:
        entry = model.Status(state=model.State.DEGRADED,
                             backend=model.Backend.CPU,
                             reason=model.Reason.NO_ACCEL)
        self.assertEqual(report.token(entry), "degraded:no-accel")

    def test_failed(self) -> None:
        entry = model.Status(state=model.State.FAILED,
                             backend=model.Backend.CPU,
                             reason=model.Reason.NO_MANIFEST)
        self.assertEqual(report.token(entry), "failed:no-manifest")


class LineTest(unittest.TestCase):
    """行式输出包含状态与后端，原因与摘要按需出现。"""

    def test_ready(self) -> None:
        entry = model.Status(state=model.State.READY,
                             backend=model.Backend.ROCM, digest=DIGEST)
        self.assertEqual(report.line(entry),
                         f"state=ready backend=rocm digest={DIGEST}")

    def test_degraded(self) -> None:
        entry = model.Status(state=model.State.DEGRADED,
                             backend=model.Backend.CPU,
                             reason=model.Reason.NO_ACCEL)
        self.assertEqual(report.line(entry),
                         "state=degraded backend=cpu reason=no-accel")

    def test_failed(self) -> None:
        entry = model.Status(state=model.State.FAILED,
                             backend=model.Backend.CPU,
                             reason=model.Reason.NO_MODEL)
        self.assertEqual(report.line(entry),
                         "state=failed backend=cpu reason=no-model")

    def test_reason_shared(self) -> None:
        entry = model.Status(state=model.State.DEGRADED,
                             backend=model.Backend.VULKAN,
                             reason=model.Reason.NO_ACCEL, digest=DIGEST)
        reason = report.token(entry).split(model.REASON_SEP, 1)[1]
        self.assertIn(f"reason={reason}", report.line(entry))


if __name__ == "__main__":
    unittest.main()
