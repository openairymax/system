# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 整机硬件清点的单元测试
#
# 用例覆盖四条判据：必检项缺失要判 fail、rfkill 关断要判 partial、设备状态
# 必须原样来自 sysfs（不翻译、不美化）、cpu 目录里混着的 cpufreq 之类不能
# 被当成 CPU。另外锁住输出顺序：cpu2 在 cpu10 之前，wlan0 在 wlan10 之前。

"""Hardware survey tests."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airymax_observe import cli, hw  # noqa: E402


class Tree(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def dev(self, rel: str, name: str, **files: str) -> Path:
        """铺一个设备目录；每个关键字是 sysfs 里的一个状态文件。"""
        path = self.base / rel / name
        path.mkdir(parents=True, exist_ok=True)
        for key, text in files.items():
            (path / key).write_text(text + "\n", encoding="ascii")
        return path

    def cpu(self, index: int, online: str = "1") -> Path:
        """x86 没有 /sys/class/cpu，CPU 只出现在 devices/system/cpu 下。"""
        return self.dev("devices/system/cpu", f"cpu{index}", online=online)

    def machine(self, cpus: int = 2) -> None:
        for index in range(cpus):
            self.cpu(index)
        self.dev("class/block", "nvme0n1")
        self.dev("class/net", "wlan0", operstate="up")


class Survey(Tree):
    def collect(self) -> hw.Survey:
        return hw.collect(self.base)

    def test_absent_required(self) -> None:
        """三个必检项一个都没有时全部记入 missing。"""
        found = self.collect()
        self.assertEqual(found.missing, ("cpu", "block", "net"))
        self.assertEqual(found.total, 0)

    def test_complete(self) -> None:
        """必检项齐全时状态为 ok，设备状态原样取自 sysfs。"""
        self.machine()
        found = self.collect()
        self.assertEqual(found.missing, ())
        self.assertEqual(found.blocked, ())
        net = _group(found, "net")
        self.assertEqual(net.entries[0].name, "wlan0")
        self.assertEqual(net.entries[0].state, "up")

    def test_cpu_filter(self) -> None:
        """同一目录下混着 cpufreq 与 cpuidle，只留 cpuN。"""
        self.machine(cpus=3)
        (self.base / "devices/system/cpu/cpufreq").mkdir()
        (self.base / "devices/system/cpu/cpuidle").mkdir()
        self.assertEqual(len(_group(self.collect(), "cpu").entries), 3)

    def test_gpu_filter(self) -> None:
        """显卡只留 cardN，DRM 连接器的 card0-HDMI 不是显卡。"""
        self.dev("class/drm", "card0")
        self.dev("class/drm", "card0-HDMI-A-1")
        names = [entry.name for entry in _group(self.collect(), "gpu").entries]
        self.assertEqual(names, ["card0"])

    def test_cpu_offline(self) -> None:
        """online=0 的核是关着的，计入 blocked 而不是当作正常。"""
        self.machine()
        self.cpu(2, online="0")
        self.assertEqual(self.collect().blocked, ("cpu/cpu2",))

    def test_block_present(self) -> None:
        """block 设备没有可读的状态文件时按 present 呈现，不伪造 up。"""
        self.machine()
        self.assertEqual(_group(self.collect(), "block").entries[0].state,
                         "present")

    def test_radio_blocked(self) -> None:
        """rfkill 软关断是可以启用的状态，记为 blocked 并判 partial。"""
        self.machine()
        self.dev("class/rfkill", "rfkill0", type="bluetooth", soft="1",
                 hard="0")
        found = self.collect()
        self.assertEqual(found.blocked, ("rfkill/rfkill0(bluetooth)",))
        self.assertEqual(cli.main(["--root", str(self.base)]), cli.EXIT_PARTIAL)

    def test_radio_ready(self) -> None:
        """两个开关都为 0 时收音机可用，不判 partial。"""
        self.machine()
        self.dev("class/rfkill", "rfkill0", type="wlan", soft="0", hard="0")
        self.assertEqual(self.collect().blocked, ())

    def test_order(self) -> None:
        """设备顺序跟硬件编号一致：cpu2 在 cpu10 之前。"""
        for index in (10, 2, 1):
            self.cpu(index)
        names = [entry.name for entry in _group(self.collect(), "cpu").entries]
        self.assertEqual(names, ["cpu1", "cpu2", "cpu10"])

    def test_optional_absent(self) -> None:
        """可选类整类缺失只标记 absent，不影响门禁结论。"""
        self.machine()
        found = self.collect()
        self.assertTrue(_group(found, "tpm").absent)
        self.assertEqual(cli.main(["--root", str(self.base)]), 0)

    def test_driver(self) -> None:
        """驱动名取自 driver 链接，用于区分「有网卡」与「网卡有驱动」。"""
        self.machine()
        link = self.base / "class/net/wlan0/device"
        link.mkdir(parents=True)
        driver = self.base / "bus/pci/drivers/iwlwifi"
        driver.mkdir(parents=True)
        (link / "driver").symlink_to(driver)
        self.assertEqual(_group(self.collect(), "net").entries[0].driver,
                         "iwlwifi")


class Output(Tree):
    def run_cli(self, *argv: str) -> tuple[int, str]:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = cli.main(list(argv))
        return code, stream.getvalue()

    def test_text(self) -> None:
        """文本输出一行汇总加每类一行，缺失的类如实写 absent。"""
        self.machine()
        code, text = self.run_cli("--root", str(self.base))
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("airy-observe state=ok"))
        self.assertIn("  cpu         2  cpu0=online cpu1=online", text)
        self.assertIn("absent", text)

    def test_json(self) -> None:
        """JSON 给出门禁可读的 missing 与 blocked 两个数组。"""
        self.machine()
        code, text = self.run_cli("--json", "--root", str(self.base))
        self.assertEqual(code, 0)
        body = json.loads(text)
        self.assertEqual(body["missing"], [])
        self.assertEqual(body["blocked"], [])
        self.assertEqual(body["groups"]["net"]["devices"][0]["name"], "wlan0")

    def test_missing_code(self) -> None:
        """必检项缺失时退出码为 1，state 为 missing。"""
        code, text = self.run_cli("--root", str(self.base))
        self.assertEqual(code, cli.EXIT_FAIL)
        self.assertIn("state=missing", text)
        self.assertIn("missing cpu, block, net", text)


def _group(found: hw.Survey, name: str) -> hw.Group:
    for group in found.groups:
        if group.name == name:
            return group
    raise AssertionError(f"no group {name}")


if __name__ == "__main__":
    unittest.main()