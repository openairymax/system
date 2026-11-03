# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 整机负载采样与联合判定回归测试
#
# 覆盖六条路径：PSI 优先、PSI 不可读时回退 proc、loadavg 按核数归一、内存
# 已用占比、swap 单独计分、以及负载介入后的档位选择与一次性压力告警。用例
# 全部跑在临时机器上，不读宿主机的 /proc，广播与关机命令都指向假脚本。

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from airymax_power import cli, guard, load  # noqa: E402
from airymax_power.thermal import Severity  # noqa: E402
from airymax_power.tuning import State  # noqa: E402
from power_fixture import Tree  # noqa: E402


class SampleTest(Tree):
    """PSI 与 proc 两条采样路径及其回退规则。"""

    def test_psi_preferred(self) -> None:
        self.psi(cpu=10.0, memory=60.0, io=5.0)
        found = load.sample(self.policy())
        self.assertEqual(found.origin, "psi")
        self.assertEqual((found.cpu, found.memory, found.io), (10.0, 60.0, 5.0))
        self.assertIs(found.strain, Severity.HOT)

    def test_psi_fallback(self) -> None:
        """PSI 目录在但三条都读不到时回退，不能因此失去负载输入。"""
        (self.base / "psi").mkdir()
        (self.base / "psi" / "cpu").write_text("", encoding="ascii")
        found = load.sample(self.policy())
        self.assertEqual(found.origin, "proc")
        self.assertIsNotNone(found.cpu)

    def test_load_normalised(self) -> None:
        """1 分钟负载按核数归一：4 核 6.0 即 150%，已到 hot。"""
        self.loadavg(6.0)
        found = load.sample(self.policy())
        self.assertEqual(found.cpu, 150.0)
        self.assertIs(found.strain, Severity.HOT)

    def test_memory_usage(self) -> None:
        self.meminfo(total=1000, available=90)
        found = load.sample(self.policy())
        self.assertEqual(found.memory, 91.0)
        self.assertIs(found.strain, Severity.HOT)

    def test_swap_alone(self) -> None:
        """内存还够但 swap 已用七成，回收路径长期活跃，同样算压力。"""
        self.meminfo(swap_total=1000, swap_free=300)
        found = load.sample(self.policy())
        self.assertEqual(found.swap, 70.0)
        self.assertIs(found.strain, Severity.HOT)

    def test_no_swap(self) -> None:
        """未配置 swap 的机器记 0，而不是永远报未知。"""
        self.meminfo(swap_total=0, swap_free=0)
        found = load.sample(self.policy())
        self.assertEqual(found.swap, 0.0)
        self.assertIs(found.strain, Severity.OK)

    def test_unknown(self) -> None:
        missing = str(self.base / "missing")
        found = load.sample(self.policy(loadavg=missing, meminfo=missing))
        self.assertIsNone(found.cpu)
        self.assertIsNone(found.memory)
        self.assertIs(found.strain, Severity.UNKNOWN)


class StrainTest(Tree):
    """负载介入后 AC 侧档位抬到 busy，电池侧与过热侧不因负载抬档。"""

    def test_busy_on_ac(self) -> None:
        self.psi(cpu=60.0)
        found = guard.apply(self.policy())
        self.assertIs(found.state, State.OK)
        self.assertIs(found.load.strain, Severity.WARN)
        self.assertIn("balanced", self.profile.read_text())
        self.assertEqual(self._epp(), "balance_performance\n")

    def test_idle_keeps_power(self) -> None:
        found = guard.apply(self.policy())
        self.assertIs(found.load.strain, Severity.OK)
        self.assertEqual(self._epp(), "balance_power\n")

    def test_battery_not_lifted(self) -> None:
        """电池上高负载不抬档，续航优先，掉电由受控关机兜住。"""
        self.battery(capacity=80)
        self.psi(cpu=90.0)
        guard.apply(self.policy())
        self.assertIn("low-power", self.profile.read_text())
        self.assertEqual(self._epp(), "balance_power\n")

    def test_hot_beats_busy(self) -> None:
        self.psi(cpu=90.0)
        self.zone("SEN1", 95000, critical=100000, index=1)
        found = guard.apply(self.policy())
        self.assertIs(found.severity, Severity.HOT)
        self.assertIn("low-power", self.profile.read_text())
        self.assertEqual(self._epp(), "power\n")

    def _epp(self) -> str:
        node = self.base / "cpu" / "policy0" / "energy_performance_preference"
        return node.read_text(encoding="ascii")


class AlertTest(Tree):
    """内存压力告警只在等级变化时广播一次，只读评估不广播。"""

    def test_pressure_once(self) -> None:
        self.psi(memory=70.0)
        guard.apply(self.policy())
        guard.apply(self.policy())
        self.assertEqual(len(self.calls("wall")), 1)
        self.assertEqual(self._press(), "hot")

    def test_release_and_rearm(self) -> None:
        self.psi(memory=70.0)
        guard.apply(self.policy())
        self.psi(memory=0.0)
        guard.apply(self.policy())
        self.assertEqual(self._press(), "ok")
        self.psi(memory=70.0)
        guard.apply(self.policy())
        self.assertEqual(len(self.calls("wall")), 2)

    def test_swap_alone_presses(self) -> None:
        """内存有余量但 swap 过线时同样告警，判据与档位共用 strain。"""
        self.meminfo(swap_total=1000, swap_free=200)
        guard.apply(self.policy())
        wall = self.calls("wall")
        self.assertEqual(len(wall), 1)
        self.assertIn("swap 80.0%", wall[0])

    def test_inspect_silent(self) -> None:
        self.psi(memory=70.0)
        guard.inspect(self.policy())
        self.assertEqual(self.calls("wall"), [])

    def _press(self) -> str:
        path = self.base / "press"
        return path.read_text(encoding="ascii").strip()


class CliTest(Tree):
    """命令行把负载作为独立证据打印，缺读数时显示短横而非 0。"""

    def test_status(self) -> None:
        self.psi(cpu=12.0, memory=5.0)
        text = self._run(self.args("status"))[1]
        self.assertIn("load origin=psi cpu=12.0% memory=5.0%", text)
        self.assertIn("strain=ok", text)

    def test_json(self) -> None:
        self.psi(memory=70.0)
        self._run(self.args("apply"))
        code, text = self._run(self.args("status") + ["--json"])
        self.assertEqual(code, 0)
        found = json.loads(text)["load"]
        self.assertEqual((found["origin"], found["strain"]), ("psi", "hot"))

    def test_missing(self) -> None:
        missing = str(self.base / "missing")
        argv = self.args("status")
        for flag in ("--psi", "--loadavg", "--meminfo"):
            argv[argv.index(flag) + 1] = missing
        text = self._run(argv)[1]
        self.assertIn("cpu=- memory=- io=- swap=- strain=unknown", text)

    @staticmethod
    def _run(argv: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()


if __name__ == "__main__":
    unittest.main()
