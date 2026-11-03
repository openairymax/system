# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 电源与温度测试共用的临时机器
#
# 判定层与命令行层需要同一棵「带固件档位、cpufreq、热区、节流计数与负载
# 采样点」的临时机器。把它收在一处，两个测试文件不再各抄一份目录拼装逻辑；
# 默认值取 285H 的现场形状——performance 档位、performance governor、一个
# 贴着 critical 门限的热区——用例只需改动自己关心的那一处。负载默认走
# proc 回退路径，因为 285H 的内核没有 PSI；需要 PSI 的用例自己造三条节点。

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
        self.cores()
        self.loadavg()
        self.meminfo()
        self.poweroff = self.fake("poweroff")
        self.wall = self.fake("wall")

    def policy(self, **over: object) -> policy.Policy:
        fields = {
            "profile": str(self.profile),
            "choices": str(self.choices),
            "cpufreq": str(self.base / "cpu"),
            "cpus": str(self.base / "cpus"),
            "supply": str(self.base / "supply"),
            "thermal": str(self.base / "thermal"),
            "throttle": str(self.base / "throttle"),
            "psi": str(self.base / "psi"),
            "loadavg": str(self.base / "loadavg"),
            "meminfo": str(self.base / "meminfo"),
            "state": str(self.base / "state"),
            "press": str(self.base / "press"),
            "duty": str(self.base / "duty"),
            "poweroff": self.poweroff,
            "wall": self.wall,
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

    def both(self, status: str = "Discharging", capacity: int = 80,
             online: str = "1") -> None:
        """适配器与电池同时存在，用于净放电与充电两种现场。"""
        self.mains(online)
        self._supply("bat", "Battery",
                     {"status": status, "capacity": str(capacity)})

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

    def device(self, index: int, label: str = "Fan", limit: int = 1,
               state: str | None = "0") -> None:
        """铺一个散热设备；state=None 表示 cur_state 读不出来（285H 现场）。"""
        path = self.base / "thermal" / f"cooling_device{index}"
        path.mkdir(parents=True, exist_ok=True)
        self._write(path / "type", label)
        self._write(path / "max_state", str(limit))
        if state is not None:
            self._write(path / "cur_state", state)

    def throttle(self, package: int = 0, core: int = 0) -> None:
        path = self.base / "throttle"
        path.mkdir(parents=True, exist_ok=True)
        self._write(path / "package_throttle_count", str(package))
        self._write(path / "core_throttle_count", str(core))

    def cores(self, count: int = 4) -> None:
        path = self.base / "cpus"
        for index in range(count):
            (path / f"cpu{index}").mkdir(parents=True, exist_ok=True)

    def idle(self, count: int = 4, depth: int = 1048) -> None:
        """按 285H 现场铺四个空闲态，最深态的退出延迟可改。"""
        for index in range(count):
            path = self.base / "cpus" / f"cpu{index}" / "cpuidle"
            for slot, (name, latency) in enumerate(
                    (("POLL", 0), ("C1_ACPI", 1), ("C2_ACPI", 127),
                     ("C3_ACPI", depth))):
                self._slot(path, slot, name, latency)

    def _slot(self, path: Path, index: int, name: str, latency: int,
              disable: int = 0) -> None:
        entry = path / f"state{index}"
        entry.mkdir(parents=True, exist_ok=True)
        self._write(entry / "name", name)
        self._write(entry / "latency", str(latency))
        self._write(entry / "disable", str(disable))

    def loadavg(self, value: float = 0.10) -> None:
        self._write(self.base / "loadavg", f"{value:.2f} 0.10 0.10 1/100 1")

    def meminfo(self, total: int = 32000000, available: int = 24000000,
                swap_total: int = 8000000, swap_free: int = 8000000) -> None:
        lines = (
            f"MemTotal:       {total} kB",
            f"MemAvailable:   {available} kB",
            f"SwapTotal:      {swap_total} kB",
            f"SwapFree:       {swap_free} kB",
        )
        self._write(self.base / "meminfo", "\n".join(lines))

    def psi(self, cpu: float = 0.0, memory: float = 0.0,
            io: float = 0.0) -> None:
        path = self.base / "psi"
        path.mkdir(parents=True, exist_ok=True)
        for name, value in (("cpu", cpu), ("memory", memory), ("io", io)):
            self._write(path / name, _psi_text(value))

    def args(self, cmd: str) -> list[str]:
        """把子命令接到本机临时路径上。"""
        return [
            cmd,
            "--profile", str(self.profile),
            "--choices", str(self.choices),
            "--cpufreq", str(self.base / "cpu"),
            "--cpus", str(self.base / "cpus"),
            "--supply", str(self.base / "supply"),
            "--thermal", str(self.base / "thermal"),
            "--throttle", str(self.base / "throttle"),
            "--psi", str(self.base / "psi"),
            "--loadavg", str(self.base / "loadavg"),
            "--meminfo", str(self.base / "meminfo"),
            "--state", str(self.base / "state"),
            "--press", str(self.base / "press"),
            "--duty", str(self.base / "duty"),
            "--poweroff", self.poweroff,
            "--wall", self.wall,
        ]

    def fake(self, name: str) -> str:
        """生成一个把参数追加到日志的假命令，避免真的关机或广播。"""
        path = self.base / f"fake-{name}"
        path.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> '{self.log(name)}'\n",
            encoding="ascii",
        )
        path.chmod(0o755)
        return str(path)

    def log(self, name: str) -> Path:
        return self.base / f"{name}.log"

    def calls(self, name: str) -> list[str]:
        """读回假命令收到的参数行；没有调用返回空表。"""
        path = self.log(name)
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8").splitlines()

    def _supply(self, name: str, kind: str, fields: dict[str, str]) -> None:
        path = self.base / "supply" / name
        path.mkdir(parents=True, exist_ok=True)
        self._write(path / "type", kind)
        for key, value in fields.items():
            self._write(path / key, value)

    @staticmethod
    def _write(path: Path, value: str) -> None:
        path.write_text(f"{value}\n", encoding="ascii")


def _psi_text(value: float) -> str:
    """按内核格式写一条 PSI 节点；判定只读 some 行的 avg10。"""
    return (f"some avg10={value:.2f} avg60=0.00 avg300=0.00 total=0\n"
            f"full avg10={value:.2f} avg60=0.00 avg300=0.00 total=0")
