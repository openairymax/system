# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 桌面与实时共存策略回归测试
#
# 用假 systemctl 把「systemd 状态」变成文件，用例因此能在没有会话总线
# 的机器上完整走通读取、判定、写入、回读四步。重点覆盖四种「没动」的
# 区别：桌面 slice 不存在（absent）是正常状态；查询失败（failed）是故障；
# 权重已达标（applied）与权重漂移（未 applied）必须给出不同退出码。
# 阈值边界单独验证，避免 20% 被写成严格大于而永远差一个样本。

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airymax_rt import cli, coexist  # noqa: E402
from rt_fixture import Machine, Shim  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name)
        self.machine = Machine(self.base)
        self.machine.pressure("0.00")
        self.shim = Shim(self.base)

    def config(self, **changes) -> coexist.Coexist:
        cfg = coexist.Coexist(
            unit="airymax-desktop.slice",
            systemctl=str(self.shim.exe),
            pressure=str(self.machine.proc / "pressure" / "cpu"),
            loadavg=str(self.machine.proc / "loadavg"),
            roots=self.machine.roots(),
        )
        return dataclasses.replace(cfg, **changes)

    def fallback(self, load1: float = 0.0, cores: int = 4) -> None:
        """去掉 PSI，只留负载均值与核数：模拟没有编入 PSI 的内核。"""
        (self.machine.proc / "pressure" / "cpu").unlink()
        self.machine.loadavg(load1)
        self.machine.cpus(cores)

    def full(self) -> None:
        self.machine.budget()
        self.machine.deadline()
        self.machine.governor()
        self.machine.cmdline.write_text(
            "threadirqs isolcpus=2,3", encoding="ascii"
        )


class DecideTest(unittest.TestCase):
    """阈值判定与读不到时的保守行为。"""

    def test_below_limit(self) -> None:
        self.assertEqual(
            coexist.decide(19.99, coexist.LIMIT),
            (coexist.Mode.NORMAL, coexist.BASE_WEIGHT),
        )

    def test_at_limit(self) -> None:
        self.assertEqual(
            coexist.decide(20.0, coexist.LIMIT),
            (coexist.Mode.PROTECT, coexist.PROTECT_WEIGHT),
        )

    def test_unreadable(self) -> None:
        self.assertEqual(
            coexist.decide(None, coexist.LIMIT),
            (coexist.Mode.NORMAL, coexist.BASE_WEIGHT),
        )


class InspectTest(Base):
    """只读评估的四种结论。"""

    def test_absent(self) -> None:
        shim = Shim(self.base / "absent", load="not-found")
        found = coexist.inspect(
            self.config(systemctl=str(shim.exe))
        )
        self.assertIs(found.mode, coexist.Mode.ABSENT)
        self.assertFalse(found.present)
        self.assertEqual(found.weight, 0)

    def test_in_sync(self) -> None:
        found = coexist.inspect(self.config())
        self.assertIs(found.mode, coexist.Mode.NORMAL)
        self.assertTrue(found.applied)
        self.assertEqual(found.current, coexist.BASE_WEIGHT)

    def test_drift(self) -> None:
        self.machine.pressure("25.50")
        found = coexist.inspect(self.config())
        self.assertIs(found.mode, coexist.Mode.PROTECT)
        self.assertEqual(found.weight, coexist.PROTECT_WEIGHT)
        self.assertEqual(found.current, coexist.BASE_WEIGHT)
        self.assertFalse(found.applied)

    def test_load_state_unreadable(self) -> None:
        self.shim.drop("LoadState")
        found = coexist.inspect(self.config())
        self.assertIs(found.mode, coexist.Mode.FAILED)
        self.assertIn("LoadState", found.detail)

    def test_systemctl_missing(self) -> None:
        found = coexist.inspect(
            self.config(systemctl=str(self.base / "none"))
        )
        self.assertIs(found.mode, coexist.Mode.FAILED)

    def test_pressure_missing(self) -> None:
        (self.machine.proc / "pressure" / "cpu").unlink()
        found = coexist.inspect(self.config())
        self.assertIs(found.mode, coexist.Mode.NORMAL)
        self.assertTrue(any("争用读数" in note for note in found.notes))

    def test_advisories(self) -> None:
        self.full()
        found = coexist.inspect(self.config())
        self.assertTrue(found.admit)
        self.assertEqual(len(found.notes), 0)

    def test_advice_when_normal(self) -> None:
        self.assertIn("基线", coexist.advice(coexist.Mode.NORMAL))

    def test_advice_when_protect(self) -> None:
        self.assertIn(
            "提升", coexist.advice(coexist.Mode.PROTECT)
        )


class FallbackTest(Base):
    """没有 PSI 的内核：退回按核数归一的负载均值。"""

    def test_saturated(self) -> None:
        self.fallback(load1=4.20, cores=4)
        found = coexist.inspect(self.config())
        self.assertIs(found.mode, coexist.Mode.PROTECT)
        self.assertEqual(found.origin, "loadavg")
        self.assertEqual(found.cores, 4)
        self.assertAlmostEqual(found.pressure, 105.0)

    def test_headroom(self) -> None:
        self.fallback(load1=2.00, cores=4)
        found = coexist.inspect(self.config())
        self.assertIs(found.mode, coexist.Mode.NORMAL)

    def test_io_wait_not_full(self) -> None:
        """4 核负载 3.0 只到 75%：等 IO 的虚高不该把权重抬上去。"""
        self.fallback(load1=3.00, cores=4)
        self.assertIs(coexist.inspect(self.config()).mode, coexist.Mode.NORMAL)

    def test_load_beats_nothing(self) -> None:
        """只有一个信号可用时也要判定，并说明用的是哪个。"""
        self.fallback(load1=5.00, cores=2)
        found = coexist.apply(self.config())
        self.assertEqual(found.origin, "loadavg")
        self.assertTrue(found.applied)
        self.assertEqual(self.shim.weight(), str(coexist.PROTECT_WEIGHT))

    def test_both_missing(self) -> None:
        self.fallback(load1=0.0, cores=4)
        (self.machine.proc / "loadavg").unlink()
        (self.machine.proc / "cpuinfo").unlink()
        found = coexist.inspect(self.config())
        self.assertIs(found.mode, coexist.Mode.NORMAL)
        self.assertEqual(found.origin, "none")
        self.assertTrue(any("争用读数" in note for note in found.notes))

    def test_psi_wins(self) -> None:
        """PSI 可读时不再看负载均值，且阈值仍是 20%。"""
        self.machine.cpus(4)
        self.machine.loadavg(9.00)
        found = coexist.inspect(self.config())
        self.assertEqual(found.origin, "psi")
        self.assertIs(found.mode, coexist.Mode.NORMAL)


class ApplyTest(Base):
    """写入路径：只在需要时写，并回读确认。"""

    def test_raise_weight(self) -> None:
        self.machine.pressure("30.00")
        found = coexist.apply(self.config())
        self.assertTrue(found.applied)
        self.assertEqual(found.current, coexist.PROTECT_WEIGHT)
        self.assertEqual(self.shim.weight(), str(coexist.PROTECT_WEIGHT))

    def test_keep_weight(self) -> None:
        found = coexist.apply(self.config())
        self.assertTrue(found.applied)
        self.assertEqual(self.shim.weight(), str(coexist.BASE_WEIGHT))

    def test_write_denied(self) -> None:
        self.shim.deny_set()
        self.machine.pressure("30.00")
        found = coexist.apply(self.config())
        self.assertFalse(found.applied)
        self.assertEqual(found.detail, "set-property 未生效")

    def test_absent_untouched(self) -> None:
        shim = Shim(self.base / "absent", load="not-found")
        found = coexist.apply(self.config(systemctl=str(shim.exe)))
        self.assertIs(found.mode, coexist.Mode.ABSENT)
        self.assertFalse(found.applied)


class CliTest(Base):
    """命令行退出码与 --json 形状。"""

    def call(self, argv: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()

    def args(self, cmd: str) -> list[str]:
        return [
            cmd,
            "--sysroot", str(self.machine.sys),
            "--procroot", str(self.machine.proc),
            "--cgroup", str(self.machine.cgroup),
            "--cmdline", str(self.machine.cmdline),
            "--systemctl", str(self.shim.exe),
            "--desktop", "airymax-desktop.slice",
            "--pressure", str(self.machine.proc / "pressure" / "cpu"),
            "--loadavg", str(self.machine.proc / "loadavg"),
        ]

    def test_in_sync_exit_zero(self) -> None:
        code, out = self.call(self.args("coexist"))
        self.assertEqual(code, 0)
        self.assertIn("mode=normal", out)

    def test_drift_exit_partial(self) -> None:
        self.machine.pressure("30.00")
        code, out = self.call(self.args("coexist"))
        self.assertEqual(code, cli.EXIT_PARTIAL)
        self.assertIn("applied=False", out)

    def test_apply_exit_zero(self) -> None:
        self.machine.pressure("30.00")
        code, out = self.call(["--apply"] + self.args("coexist"))
        self.assertEqual(code, 0)
        self.assertIn("applied=True", out)
        self.assertEqual(self.shim.weight(), str(coexist.PROTECT_WEIGHT))

    def test_failed_exit_fail(self) -> None:
        self.shim.drop("LoadState")
        code, _ = self.call(self.args("coexist"))
        self.assertEqual(code, cli.EXIT_FAIL)

    def test_json_shape(self) -> None:
        code, out = self.call(["--json"] + self.args("coexist"))
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["mode"], "normal")
        self.assertEqual(payload["unit"], "airymax-desktop.slice")
        self.assertEqual(payload["weight"], coexist.BASE_WEIGHT)
        self.assertEqual(payload["origin"], "psi")
        self.assertIn("admit", payload)
        self.assertIsInstance(payload["notes"], list)

    def test_json_fallback_origin(self) -> None:
        self.fallback(load1=4.10, cores=4)
        code, out = self.call(["--json"] + self.args("coexist"))
        self.assertEqual(code, cli.EXIT_PARTIAL)
        payload = json.loads(out)
        self.assertEqual(payload["origin"], "loadavg")
        self.assertEqual(payload["cores"], 4)
        self.assertEqual(payload["mode"], "protect")


if __name__ == "__main__":
    unittest.main()
