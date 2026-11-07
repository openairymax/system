# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 首启网络自检脚本行为回归测试
#
# 在临时目录里构造 sysfs、/proc、内核日志与假命令，逐一命中五个失败态，
# 断言单行输出格式、状态文件落位与权限，以及判定过程不改写 sysfs。

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import units_lint  # noqa: E402

SCRIPT = ROOT / "scripts" / "net_doctor.sh"
LINE = re.compile(
    r"^AIRYMAX-NET state=(?P<state>ok|no-device|no-firmware|no-carrier"
    r"|no-ssid|dhcp-timeout) dev=(?P<dev>\S+) driver=(?P<driver>\S+)"
    r" fw=(?P<fw>\S+) rfkill=(?P<rfkill>\d+) hint=(?P<hint>\S+)$"
)

NMCLI = """#!/bin/bash
case "$*" in
    "-t -f RUNNING general")
        if [ "${AIRY_FAKE_NM:-running}" = running ]; then
            printf 'running\\n'
        else
            exit 1
        fi
        ;;
    "-t -f IP4.ADDRESS device show "*)
        if [ -n "${AIRY_FAKE_ADDR:-}" ]; then
            printf 'IP4.ADDRESS[1]:%s/24\\n' "${AIRY_FAKE_ADDR}"
        fi
        ;;
    "-t -f GENERAL.STATE device show "*)
        printf 'GENERAL.STATE:100 (connected)\\n'
        ;;
esac
exit 0
"""

IP = """#!/bin/bash
dev=''
for arg in "$@"; do
    dev="${arg}"
done
if [ -n "${AIRY_FAKE_ADDR:-}" ]; then
    printf '2: %s    inet %s/24 scope global %s\\n' \\
        "${dev}" "${AIRY_FAKE_ADDR}" "${dev}"
fi
"""

IW = """#!/bin/bash
printf '%s' "${AIRY_FAKE_SCAN:-}"
exit "${AIRY_FAKE_IW_RC:-0}"
"""


class NetDoctorCase(unittest.TestCase):
    """构造隔离的运行期事实并驱动脚本。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.sys = self.base / "sys"
        self.proc = self.base / "proc"
        self.runroot = self.base / "run" / "airymaxos"
        self.bin = self.base / "bin"
        self.dmesg = self.base / "dmesg"
        for path in (
            self.sys / "class" / "net",
            self.sys / "class" / "rfkill",
            self.sys / "bus",
            self.proc,
            self.bin,
        ):
            path.mkdir(parents=True)
        (self.proc / "uptime").write_text("12.34 4.56\n", encoding="utf-8")
        self.dmesg.write_text("", encoding="utf-8")
        for name, body in (("nmcli", NMCLI), ("ip", IP), ("iw", IW)):
            path = self.bin / name
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
        self.env = dict(
            os.environ,
            PATH=f"{self.bin}:{os.environ['PATH']}",
            AIRY_SYSFS_ROOT=str(self.sys),
            AIRY_PROC_ROOT=str(self.proc),
            AIRY_RUN_DIR=str(self.runroot),
            AIRY_DMESG_FILE=str(self.dmesg),
            AIRY_FAKE_NM="running",
            AIRY_FAKE_ADDR="",
            AIRY_FAKE_SCAN="",
            AIRY_FAKE_IW_RC="0",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def driver_dir(self, name: str) -> Path:
        path = self.sys / "bus" / "pci" / "drivers" / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def add_dev(self, name: str, carrier: int, driver: str,
                wl: bool = False) -> None:
        dev = self.sys / "class" / "net" / name
        dev.mkdir()
        (dev / "carrier").write_text(f"{carrier}\n", encoding="utf-8")
        if driver:
            (dev / "device").mkdir()
            (dev / "device" / "driver").symlink_to(
                self.driver_dir(driver))
        if wl:
            (dev / "phy80211").mkdir()

    def add_bus(self, driver: str = "") -> None:
        dev = self.sys / "bus" / "pci" / "devices" / "0000:01:00.0"
        dev.mkdir(parents=True)
        (dev / "class").write_text("0x028000\n", encoding="utf-8")
        (self.sys / "bus" / "pci" / "0000:01:00.0").symlink_to(dev)
        if driver:
            (dev / "driver").symlink_to(self.driver_dir(driver))

    def add_rfkill(self, soft: int, hard: int) -> None:
        path = self.sys / "class" / "rfkill" / "rfkill0"
        path.mkdir()
        (path / "soft").write_text(f"{soft}\n", encoding="utf-8")
        (path / "hard").write_text(f"{hard}\n", encoding="utf-8")

    def add_supp(self, pid: str = "4242") -> None:
        path = self.proc / pid
        path.mkdir()
        (path / "comm").write_text("wpa_supplicant\n", encoding="utf-8")

    def kernel(self, *lines: str) -> None:
        self.dmesg.write_text(
            "".join(f"{line}\n" for line in lines), encoding="utf-8")

    def snapshot(self) -> dict[str, tuple[str, object]]:
        seen: dict[str, tuple[str, object]] = {}
        for path in sorted(self.sys.rglob("*")):
            rel = str(path.relative_to(self.sys))
            if path.is_symlink():
                seen[rel] = ("link", os.readlink(path))
            elif path.is_dir():
                seen[rel] = ("dir", b"")
            else:
                seen[rel] = ("file", path.read_bytes())
        return seen

    def run_doctor(self, **extra: str) -> subprocess.CompletedProcess[str]:
        env = dict(self.env, **extra)
        return subprocess.run(
            ["bash", str(SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def verdict(self, out: subprocess.CompletedProcess[str],
                state: str) -> dict[str, str]:
        self.assertEqual(out.returncode, 0 if state == "ok" else 3,
                         out.stderr)
        self.assertEqual(len(out.stdout.splitlines()), 1, out.stdout)
        match = LINE.fullmatch(out.stdout.rstrip("\n"))
        self.assertIsNotNone(match, out.stdout)
        assert match is not None
        self.assertEqual(match.group("state"), state)
        return match.groupdict()

    def check_state(self, out: subprocess.CompletedProcess[str]) -> None:
        state = self.runroot / "net.state"
        self.assertEqual(state.read_text(encoding="utf-8"), out.stdout)
        self.assertEqual(stat.S_IMODE(self.runroot.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o644)

    def test_no_device(self) -> None:
        out = self.run_doctor()
        fields = self.verdict(out, "no-device")
        self.assertEqual(fields["dev"], "-")
        self.assertEqual(fields["driver"], "-")
        self.assertEqual(fields["fw"], "-")
        self.assertEqual(fields["rfkill"], "0")
        self.assertIn("netdev=0", fields["hint"])
        self.check_state(out)

    def test_no_device_reports_dt_node(self) -> None:
        node = self.sys / "firmware" / "devicetree" / "base" / "wifi@18800000"
        node.mkdir(parents=True)
        (node / "compatible").write_text(
            "qcom,wcn3990-wifi\n", encoding="utf-8")
        fields = self.verdict(self.run_doctor(), "no-device")
        self.assertIn("dt=wifi@18800000/compatible", fields["hint"])

    def test_no_device_reports_unbound_bus_device(self) -> None:
        self.add_bus()
        fields = self.verdict(self.run_doctor(), "no-device")
        self.assertIn("unbound=pci/0000:01:00.0", fields["hint"])

    def test_no_firmware_before_netdev(self) -> None:
        self.add_bus("iwlwifi")
        self.kernel(
            "iwlwifi 0000:01:00.0: Direct firmware load for"
            " iwlwifi-ty-a0-gf-a0-89.ucode failed with error -2")
        out = self.run_doctor()
        fields = self.verdict(out, "no-firmware")
        self.assertEqual(fields["dev"], "-")
        self.assertEqual(fields["driver"], "iwlwifi")
        self.assertEqual(fields["fw"],
                         "missing:iwlwifi-ty-a0-gf-a0-89.ucode")
        self.assertIn("path=/usr/lib/firmware/", fields["hint"])
        self.check_state(out)

    def test_no_firmware_with_netdev(self) -> None:
        self.add_dev("wlan0", 0, "iwlwifi", wl=True)
        self.kernel(
            "iwlwifi 0000:01:00.0: Direct firmware load for"
            " iwlwifi-ty-a0-gf-a0-89.ucode failed with error -2")
        fields = self.verdict(self.run_doctor(), "no-firmware")
        self.assertEqual(fields["dev"], "wlan0")
        self.assertEqual(fields["driver"], "iwlwifi")
        self.assertIn("dev=wlan0", fields["hint"])

    def test_retried_firmware_is_not_missing(self) -> None:
        self.add_dev("wlan0", 0, "iwlwifi", wl=True)
        self.kernel(
            "iwlwifi 0000:01:00.0: Direct firmware load for"
            " iwlwifi-ty-a0-gf-a0-89.ucode failed with error -2",
            "iwlwifi 0000:01:00.0: direct-loading firmware"
            " iwlwifi-ty-a0-gf-a0-89.ucode")
        fields = self.verdict(self.run_doctor(), "no-carrier")
        self.assertEqual(fields["fw"], "-")

    def test_no_carrier_counts_rfkill(self) -> None:
        self.add_dev("eth0", 0, "e1000e")
        self.add_rfkill(1, 0)
        out = self.run_doctor()
        fields = self.verdict(out, "no-carrier")
        self.assertEqual(fields["dev"], "eth0")
        self.assertEqual(fields["driver"], "e1000e")
        self.assertEqual(fields["rfkill"], "1")
        self.assertEqual(fields["hint"], "carrier=0,soft=1,hard=0")
        self.check_state(out)

    def test_no_ssid(self) -> None:
        self.add_dev("wlan0", 0, "iwlwifi", wl=True)
        self.add_supp()
        fields = self.verdict(self.run_doctor(), "no-ssid")
        self.assertEqual(fields["dev"], "wlan0")
        self.assertEqual(fields["hint"], "scans=1,ap=0,err=none")

    def test_dhcp_timeout(self) -> None:
        self.add_dev("eth0", 1, "e1000e")
        fields = self.verdict(self.run_doctor(), "dhcp-timeout")
        self.assertEqual(fields["dev"], "eth0")
        self.assertEqual(fields["hint"], "wait=12,nmcli=100")

    def test_ok_falls_back_to_ip_when_nm_down(self) -> None:
        self.add_dev("eth0", 1, "e1000e")
        out = self.run_doctor(AIRY_FAKE_NM="down",
                              AIRY_FAKE_ADDR="192.0.2.10")
        fields = self.verdict(out, "ok")
        self.assertEqual(fields["dev"], "eth0")
        self.assertEqual(fields["hint"], "addr=192.0.2.10")
        self.check_state(out)

    def test_runtime_dir_mode_is_corrected(self) -> None:
        self.runroot.mkdir(parents=True)
        self.runroot.chmod(0o700)
        self.check_state(self.run_doctor())

    def test_sysfs_is_never_written(self) -> None:
        self.add_dev("wlan0", 0, "iwlwifi", wl=True)
        self.add_rfkill(0, 1)
        before = self.snapshot()
        self.verdict(self.run_doctor(), "no-carrier")
        self.assertEqual(self.snapshot(), before)

    def test_lint_ok(self) -> None:
        self.assertEqual(units_lint.check_script(SCRIPT), [])


if __name__ == "__main__":
    unittest.main()
