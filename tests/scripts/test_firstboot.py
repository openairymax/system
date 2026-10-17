# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 首启初始化脚本行为回归测试
#
# 在临时根目录中构造 machine-id、sysctl 配置、LSM 探针与完成标记，
# 并以 PATH 注入的假 sysctl、modprobe、systemctl 与
# systemd-machine-id-setup 驱动脚本分支，验证五步初始化、幂等语义
# 与六种退出码契约。

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import units_lint  # noqa: E402

SCRIPT = ROOT / "scripts" / "firstboot.sh"
MID = "0123456789abcdef0123456789abcdef"

SYSCTL = """#!/usr/bin/env bash
set -euo pipefail
printf 'sysctl %s\\n' "$*" >>"${FAKE_LOG:?}"
exit "${FAKE_SYSCTL_RC:-0}"
"""

MODPROBE = """#!/usr/bin/env bash
set -euo pipefail
printf 'modprobe %s\\n' "$*" >>"${FAKE_LOG:?}"
if [ -n "${FAKE_MODPROBE_PROBE:-}" ]; then
    printf '1\\n' >"${FAKE_MODPROBE_PROBE}"
fi
exit "${FAKE_MODPROBE_RC:-0}"
"""

SYSTEMCTL = """#!/usr/bin/env bash
set -euo pipefail
printf 'systemctl %s\\n' "$*" >>"${FAKE_LOG:?}"
exit "${FAKE_SYSTEMCTL_RC:-0}"
"""

MIDSETUP = """#!/usr/bin/env bash
set -euo pipefail
printf 'machine-id-setup %s\\n' "$*" >>"${FAKE_LOG:?}"
[ "${FAKE_MIDSETUP_RC:-0}" = 0 ] || exit "${FAKE_MIDSETUP_RC}"
printf '%s\\n' "${FAKE_MID:?}" >"${FAKE_MID_DIR:?}/machine-id"
"""


class FirstbootCase(unittest.TestCase):
    """构造隔离根目录与假命令环境。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "root"
        self.bin = base / "bin"
        self.etc = self.root / "etc"
        self.state = self.root / "var" / "lib" / "airymaxos"
        self.probe = self.root / "proc" / "sys" / "agentrt" / "present"
        self.uuid = self.root / "proc" / "sys" / "kernel" / "random" / "uuid"
        self.log = base / "calls.log"
        self.etc.mkdir(parents=True)
        self.state.mkdir(parents=True)
        self.probe.parent.mkdir(parents=True)
        self.uuid.parent.mkdir(parents=True)
        self.bin.mkdir()
        self.fake("sysctl", SYSCTL)
        self.fake("modprobe", MODPROBE)
        self.fake("systemctl", SYSTEMCTL)
        self.fake("systemd-machine-id-setup", MIDSETUP)
        self.mid(MID)
        self.ready()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def fake(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    def mid(self, value: str) -> Path:
        path = self.etc / "machine-id"
        path.write_text(value + "\n", encoding="utf-8")
        return path

    def ready(self) -> None:
        self.probe.write_text("1\n", encoding="utf-8")

    def marker(self) -> Path:
        return self.state / ".firstboot-done"

    def run_fb(self, **env: str) -> subprocess.CompletedProcess[str]:
        extra = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "AIRY_FIRSTBOOT_ROOT": str(self.root),
            "AIRY_FIRSTBOOT_SYSCTL": "sysctl",
            "AIRY_FIRSTBOOT_MODPROBE": "modprobe",
            "AIRY_FIRSTBOOT_SYSTEMCTL": "systemctl",
            "AIRY_FIRSTBOOT_MIDSETUP": "systemd-machine-id-setup",
            "AIRY_FIRSTBOOT_UUID": str(self.uuid),
            "FAKE_LOG": str(self.log),
            "FAKE_MID": MID,
            "FAKE_MID_DIR": str(self.etc),
        }
        extra.update(env)
        return subprocess.run(
            ["bash", str(SCRIPT)],
            env=dict(os.environ, **extra),
            capture_output=True,
            text=True,
            check=False,
        )

    def calls(self) -> str:
        if not self.log.exists():
            return ""
        return self.log.read_text(encoding="utf-8")


class ExitCase(FirstbootCase):
    """退出码与完成标记生命周期。"""

    def test_pass(self) -> None:
        res = self.run_fb()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(self.marker().exists())
        self.assertIn("sysctl --system --quiet", self.calls())
        self.assertIn("start --no-block agentrt.target", self.calls())

    def test_idem(self) -> None:
        self.marker().write_text("done\n", encoding="utf-8")
        res = self.run_fb()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.calls(), "")
        self.assertIn("跳过", res.stdout)

    def test_mid_setup(self) -> None:
        (self.etc / "machine-id").unlink()
        res = self.run_fb()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(
            (self.etc / "machine-id").read_text(encoding="utf-8").strip(),
            MID,
        )
        self.assertIn("machine-id-setup", self.calls())

    def test_mid_uuid(self) -> None:
        (self.etc / "machine-id").unlink()
        self.uuid.write_text(f"{MID[:8]}-{MID[8:12]}-{MID[12:16]}"
                             f"-{MID[16:20]}-{MID[20:]}\n", encoding="utf-8")
        res = self.run_fb(FAKE_MIDSETUP_RC="1")
        self.assertEqual(res.returncode, 0, res.stderr)
        path = self.etc / "machine-id"
        self.assertEqual(path.read_text(encoding="utf-8").strip(), MID)
        self.assertEqual(path.stat().st_mode & 0o777, 0o444)

    def test_mid_bad(self) -> None:
        (self.etc / "machine-id").unlink()
        self.uuid.write_text("not-a-uuid\n", encoding="utf-8")
        res = self.run_fb(FAKE_MIDSETUP_RC="1")
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertFalse(self.marker().exists())

    def test_sysctl_fail(self) -> None:
        res = self.run_fb(FAKE_SYSCTL_RC="1")
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertFalse(self.marker().exists())

    def test_lsm_absent(self) -> None:
        self.probe.unlink()
        res = self.run_fb()
        self.assertEqual(res.returncode, 3, res.stderr)
        self.assertIn("ENOTSUP", res.stderr)
        self.assertFalse(self.marker().exists())

    def test_lsm_module(self) -> None:
        self.probe.unlink()
        res = self.run_fb(FAKE_MODPROBE_PROBE=str(self.probe))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("modprobe airy_lsm", self.calls())

    def test_start_fail(self) -> None:
        res = self.run_fb(FAKE_SYSTEMCTL_RC="1")
        self.assertEqual(res.returncode, 4, res.stderr)
        self.assertFalse(self.marker().exists())

    def test_mark_fail(self) -> None:
        self.state.rmdir()
        self.state.write_text("blocked\n", encoding="utf-8")
        res = self.run_fb()
        self.assertEqual(res.returncode, 5, res.stderr)
        self.assertFalse(self.marker().exists())


class LintCase(unittest.TestCase):
    """脚本静态契约校验器。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / SCRIPT.name
        shutil.copy2(SCRIPT, self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def assert_bad(self, needle: str) -> None:
        errs = units_lint.check_script(self.path)
        self.assertTrue(
            any(needle in err for err in errs),
            f"missing {needle!r} in {errs}",
        )

    def test_lint_ok(self) -> None:
        self.assertEqual(units_lint.check_script(self.path), [])

    def test_lint_mode(self) -> None:
        self.path.chmod(0o644)
        self.assert_bad("mode must be 0755")

    def test_shebang(self) -> None:
        text = self.path.read_text(encoding="utf-8")
        self.path.write_text(text.replace("bash", "sh", 1), encoding="utf-8")
        self.assert_bad("shebang")

    def test_set_e(self) -> None:
        text = self.path.read_text(encoding="utf-8")
        self.path.write_text(
            text.replace("set -euo pipefail", "# no flags"),
            encoding="utf-8",
        )
        self.assert_bad("set -euo pipefail")


if __name__ == "__main__":
    unittest.main()
