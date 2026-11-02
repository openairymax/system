# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 电源与温度判定回归测试
#
# 覆盖四条判定链：热区余量分级、供电来源识别、控制点落地回读、以及
# 供电加热状态到档位选择。用例全部在临时机器上跑，不需要 root，也不碰
# 宿主机的真实 sysfs。

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from airymax_power import guard, sysfs, thermal  # noqa: E402
from airymax_power.supply import Source, detect  # noqa: E402
from airymax_power.thermal import Severity, grade  # noqa: E402
from airymax_power.tuning import State  # noqa: E402
from power_fixture import Tree  # noqa: E402


class GradeTest(unittest.TestCase):
    """critical 余量与绝对温标两条分级路径。"""

    def test_margin(self) -> None:
        self.assertIs(grade(97000, 100000), Severity.CRITICAL)
        self.assertIs(grade(92000, 100000), Severity.HOT)
        self.assertIs(grade(86000, 100000), Severity.WARN)
        self.assertIs(grade(70000, 100000), Severity.OK)

    def test_absolute(self) -> None:
        self.assertIs(grade(96000, None), Severity.CRITICAL)
        self.assertIs(grade(91000, None), Severity.HOT)
        self.assertIs(grade(81000, None), Severity.WARN)
        self.assertIs(grade(60000, None), Severity.OK)

    def test_unknown(self) -> None:
        self.assertIs(grade(None, 100000), Severity.UNKNOWN)


class SupplyTest(Tree):
    """供电来源识别。"""

    def test_ac(self) -> None:
        self.assertIs(detect(self.policy()), Source.AC)

    def test_battery(self) -> None:
        self.battery()
        self.assertIs(detect(self.policy()), Source.BATTERY)

    def test_charging(self) -> None:
        self.battery("Charging")
        self.assertIs(detect(self.policy()), Source.AC)

    def test_unknown_battery(self) -> None:
        self.battery("Unknown")
        self.assertIs(detect(self.policy()), Source.BATTERY)

    def test_without_supply(self) -> None:
        self.unplug()
        self.assertIs(detect(self.policy()), Source.UNKNOWN)


class ThermalTest(Tree):
    """热区采样、分级与无效读数过滤。"""

    def test_alarm_first(self) -> None:
        self.zone("SEN1", 90000, critical=92000, index=1)
        found = thermal.collect(self.policy())
        self.assertIs(found.severity, Severity.CRITICAL)
        self.assertEqual(found.alarm.name, "SEN1")

    def test_disabled_zone(self) -> None:
        self.zone("SEN1", 99000, critical=100000, mode="disabled", index=1)
        self.assertIs(thermal.collect(self.policy()).severity, Severity.OK)

    def test_invalid_reading(self) -> None:
        self.zone("iwlwifi", -61000, index=1)
        names = [zone.name for zone in thermal.collect(self.policy()).zones]
        self.assertNotIn("iwlwifi", names)

    def test_throttle(self) -> None:
        self.throttle(16025, 7558)
        found = thermal.collect(self.policy())
        self.assertEqual((found.package, found.core), (16025, 7558))


class TuneTest(Tree):
    """档位落地与回读判定。"""

    def test_ac(self) -> None:
        found = guard.apply(self.policy())
        self.assertIs(found.state, State.OK)
        self.assertEqual(found.source, Source.AC)
        self.assertIn("balanced", self.profile.read_text())

    def test_battery(self) -> None:
        self.battery()
        found = guard.apply(self.policy())
        self.assertIs(found.state, State.OK)
        self.assertEqual(found.source, Source.BATTERY)
        self.assertIn("low-power", self.profile.read_text())

    def test_cool(self) -> None:
        self.zone("SEN1", 95000, critical=100000, index=1)
        found = guard.apply(self.policy())
        self.assertIs(found.state, State.OK)
        self.assertIs(found.severity, Severity.HOT)
        self.assertIn("low-power", self.profile.read_text())

    def test_unsupported(self) -> None:
        missing = str(self.base / "missing")
        found = guard.inspect(self.policy(profile=missing, cpufreq=missing))
        self.assertIs(found.state, State.UNSUPPORTED)

    def test_partial(self) -> None:
        self.cpu("policy1")
        real = sysfs.write_text

        def deny(path: Path, value: str) -> bool:
            return False if "policy1" in str(path) else real(path, value)

        with mock.patch.object(sysfs, "write_text", deny):
            found = guard.apply(self.policy())
        self.assertIs(found.state, State.PARTIAL)
        self.assertEqual((found.present, found.landed), (5, 3))

    def test_failed(self) -> None:
        with mock.patch.object(sysfs, "write_text", return_value=False):
            found = guard.apply(self.policy())
        self.assertIs(found.state, State.FAILED)
        self.assertEqual(found.landed, 0)
