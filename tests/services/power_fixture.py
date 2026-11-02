# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 电源与温度测试共用的临时机器
#
# 判定层与命令行层需要同一棵「带固件档位、cpufreq、热区与节流计数」的临时
# 机器。把它收在一处，两个测试文件不再各抄一份目录拼装逻辑；默认值取
# 285H 的现场形状——performance 档位、performance governor、一个贴着
# critical 门限的热区——用例只需改动自己关心的那一处。

"""Shared temporary machine for the power tests."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airymax_power import policy  # noqa: E402


class Tree(unittest.TestCase):
    """一棵临时机器：默认 AC 供电、performance 档位、一个健康热区。"""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name)
        self.profile = self.base / "profile"
        self.choices = self.base / "choices"
        self.profile.write_text("performance\n", encoding="ascii")
        self.choices.write_text("low-power balanced performance\n", "ascii")
        self.mains()
        self.zone("x86_pkg_temp", 50000)
        self.cpu()
        self.throttle()

    def policy(self, **over: object) -> policy.Policy:
        fields = {
            "profile": str(self.profile),
            "choices": str(self.choices),
            "cpufreq": str(self.base / "cpu"),
            "supply": str(self.base / "supply"),
            "thermal": str(self.base / "thermal"),
            "throttle": str(self.base / "throttle"),
        }
        fields.update(over)
        return policy.Policy(**fields)

    def mains(self, online: str = "1") -> None:
        self._supply("ac", "Mains", {"online": online})

    def battery(self, status: str = "Discharging",
                capacity: int = 80) -> None:
        shutil.rmtree(self.base / "supply" / "ac", ignore_errors=True)
        self._supply("bat", "Battery",
                     {"status": status, "capacity": str(capacity)})

    def unplug(self) -> None:
        shutil.rmtree(self.base / "supply", ignore_errors=True)

    def zone(self, name: str, temp: int, critical: int | None = None,
             mode: str = "enabled", index: int = 0) -> None:
        path = self.base / "thermal" / f"thermal_zone{index}"
        path.mkdir(parents=True, exist_ok=True)
        (path / "type").write_text(f"{name}\n", encoding="ascii")
        (path / "temp").write_text(f"{temp}\n", encoding="ascii")
        (path / "mode").write_text(f"{mode}\n", encoding="ascii")
        if critical is not None:
            (path / "trip_point_0_type").write_text("critical\n", "ascii")
            (path / "trip_point_0_temp").write_text(f"{critical}\n", "ascii")

    def cpu(self, name: str = "policy0", governor: str = "performance",
            epp: str = "performance",
            available: str = "performance powersave") -> None:
        path = self.base / "cpu" / name
        path.mkdir(parents=True, exist_ok=True)
        self._write(path / "scaling_governor", governor)
        self._write(path / "scaling_available_governors", available)
        self._write(path / "energy_performance_preference", epp)
        self._write(path / "energy_performance_available_preferences",
                    "performance balance_performance balance_power power")

    def throttle(self, package: int = 0, core: int = 0) -> None:
        path = self.base / "throttle"
        path.mkdir(parents=True, exist_ok=True)
        self._write(path / "package_throttle_count", str(package))
        self._write(path / "core_throttle_count", str(core))

    def args(self, cmd: str) -> list[str]:
        """把子命令接到本机临时路径上。"""
        return [
            cmd,
            "--profile", str(self.profile),
            "--choices", str(self.choices),
            "--cpufreq", str(self.base / "cpu"),
            "--supply", str(self.base / "supply"),
            "--thermal", str(self.base / "thermal"),
            "--throttle", str(self.base / "throttle"),
        ]

    def _supply(self, name: str, kind: str, fields: dict[str, str]) -> None:
        path = self.base / "supply" / name
        path.mkdir(parents=True, exist_ok=True)
        self._write(path / "type", kind)
        for key, value in fields.items():
            self._write(path / key, value)

    @staticmethod
    def _write(path: Path, value: str) -> None:
        path.write_text(f"{value}\n", encoding="ascii")
