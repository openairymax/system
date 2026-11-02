# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 加速探测输出契约测试
#
# probe 的两种输出是运维与门禁共用的契约：行式给人看，JSON 给脚本读。
# 字段集合、顺序与证据原样传递都要逐条钉死，否则改一处渲染就会让外部
# 解析静默失效。另断言 probe 不落盘——只读探针若顺手写状态文件，监控
# 轮询就会把「查一下有什么卡」变成「改写判定」。

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyllm import accel, cli, model, report  # noqa: E402

ROWS = (
    accel.Observation(model.Backend.CUDA, False,
                      ("missing:dev/nvidiactl", "missing:dev/nvidia0")),
    accel.Observation(model.Backend.ROCM, False, ("missing:dev/kfd",)),
    accel.Observation(model.Backend.VULKAN, True,
                      ("ok:dev/dri",
                       "unverified:usr/share/vulkan/icd.d/radeon.json")),
    accel.Observation(model.Backend.CPU, True, ("builtin",)),
)

LINE = "cuda=absent rocm=absent vulkan=present cpu=present"


class ProbeOutputTest(unittest.TestCase):
    def test_line(self) -> None:
        self.assertEqual(report.probe_line(ROWS), LINE)

    def test_fields(self) -> None:
        payload = report.probe_fields(ROWS)
        self.assertEqual(list(payload), ["backends"])
        self.assertEqual([item["backend"] for item in payload["backends"]],
                         ["cuda", "rocm", "vulkan", "cpu"])
        for item in payload["backends"]:
            self.assertEqual(list(item), ["backend", "present", "evidence"])
        self.assertEqual(payload["backends"][2]["evidence"],
                         ["ok:dev/dri",
                          "unverified:usr/share/vulkan/icd.d/radeon.json"])

    def test_cli_json(self) -> None:
        code, out = self._cli("probe", "--json")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(json.loads(out), report.probe_fields(ROWS))

    def test_cli_line(self) -> None:
        code, out = self._cli("probe")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(out.strip(), LINE)

    def test_cli_does_not_write_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._cli("--root", tmp, "probe")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def _cli(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.object(accel, "probe", return_value=ROWS), \
                redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()


if __name__ == "__main__":
    unittest.main()
