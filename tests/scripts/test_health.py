# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 启动健康自检脚本行为回归测试
#
# 在临时根目录中构造 PMEM、MGLRU 与待确认升级标记等真实文件，
# 并以 PATH 注入的假 systemctl 与假 rpm-ostree 驱动脚本分支，
# 验证五项检查、三态计数与六种退出码契约。

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

SCRIPT = ROOT / "scripts" / "health_check.sh"

SYSTEMCTL = """#!/usr/bin/env bash
set -euo pipefail
[[ ${1:-} == is-active && ${2:-} == --quiet ]] || exit 2
unit=${3:-}
case " ${FAKE_DOWN:-} " in
    *" ${unit} "*) exit 3 ;;
esac
exit 0
"""

OSTREE = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >>"${FAKE_LOG:?}"
exit "${FAKE_RC:-0}"
"""


class HealthCase(unittest.TestCase):
    """构造隔离根目录与假命令环境。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "root"
        self.bin = base / "bin"
        self.state = self.root / "var" / "lib" / "agentrt"
        self.state.mkdir(parents=True)
        self.bin.mkdir()
        self.log = base / "ostree.log"
        self.fake("systemctl", SYSTEMCTL)
        self.fake("rpm-ostree", OSTREE)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def fake(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    def run_check(self, **env: str) -> subprocess.CompletedProcess[str]:
        extra = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "AIRY_HEALTH_ROOT": str(self.root),
            "AIRY_HEALTH_OSTREE": "rpm-ostree",
            "FAKE_LOG": str(self.log),
        }
        extra.update(env)
        return subprocess.run(
            ["bash", str(SCRIPT)],
            env=dict(os.environ, **extra),
            capture_output=True,
            text=True,
            check=False,
        )

    def pending(self) -> Path:
        path = self.state / ".pending-upgrade"
        path.write_text("test\n", encoding="utf-8")
        return path

    def pmem(self) -> None:
        dev = self.root / "dev" / "pmem0"
        dev.parent.mkdir(parents=True, exist_ok=True)
        dev.write_text("", encoding="utf-8")

    def mount(self, path: str) -> None:
        info = self.root / "proc" / "self" / "mountinfo"
        info.parent.mkdir(parents=True, exist_ok=True)
        info.write_text(
            f"36 35 0:32 / {path} rw - ext4 /dev/pmem0 rw\n",
            encoding="utf-8",
        )

    def mglru(self, value: str) -> None:
        node = self.root / "sys" / "kernel" / "mm" / "lru_gen"
        node.mkdir(parents=True, exist_ok=True)
        (node / "enabled").write_text(value + "\n", encoding="utf-8")


class ExitCase(HealthCase):
    """退出码与升级标记生命周期。"""

    def test_pass(self) -> None:
        res = self.run_check()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("失败 0", res.stdout)

    def test_clear(self) -> None:
        mark = self.pending()
        res = self.run_check()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertFalse(mark.exists())
        self.assertIn("固化当前版本", res.stdout)

    def test_degraded(self) -> None:
        mark = self.pending()
        res = self.run_check(FAKE_DOWN="agentrt-mem.service")
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertTrue(mark.exists())
        self.assertIn("失败 1", res.stdout)

    def test_threshold(self) -> None:
        self.pmem()
        res = self.run_check(FAKE_DOWN="agentrt.target agentrt-mem.service")
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertIn("无待确认升级", res.stderr)

    def test_enotsup(self) -> None:
        mark = self.pending()
        self.pmem()
        res = self.run_check(
            FAKE_DOWN="agentrt.target agentrt-mem.service",
            AIRY_HEALTH_OSTREE="airy-missing-tool",
        )
        self.assertEqual(res.returncode, 3, res.stderr)
        self.assertTrue(mark.exists())
        self.assertIn("ENOTSUP", res.stderr)
        self.assertFalse(self.log.exists())

    def test_rollback(self) -> None:
        mark = self.pending()
        self.pmem()
        res = self.run_check(
            FAKE_DOWN="agentrt.target agentrt-mem.service",
        )
        self.assertEqual(res.returncode, 4, res.stderr)
        self.assertFalse(mark.exists())
        self.assertEqual(self.log.read_text(encoding="utf-8"), "rollback\n")
        self.assertIn("已回滚", res.stderr)

    def test_rb_fail(self) -> None:
        mark = self.pending()
        self.pmem()
        res = self.run_check(
            FAKE_DOWN="agentrt.target agentrt-mem.service",
            FAKE_RC="1",
        )
        self.assertEqual(res.returncode, 5, res.stderr)
        self.assertTrue(mark.exists())
        self.assertIn("回滚命令执行失败", res.stderr)


class CheckCase(HealthCase):
    """PMEM 与 MGLRU 条件检查。"""

    def test_pmem_ok(self) -> None:
        self.pmem()
        self.mount(str(self.state / "memoryrovol" / "pmem"))
        res = self.run_check()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("MemoryRovol PMEM: OK", res.stdout)

    def test_pmem_bad(self) -> None:
        self.pmem()
        self.mount("/mnt/other")
        res = self.run_check()
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("not mounted", res.stderr)

    def test_no_pmem(self) -> None:
        res = self.run_check()
        self.assertIn("no PMEM device", res.stdout)

    def test_mglru_on(self) -> None:
        self.mglru("0x0007")
        res = self.run_check()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("MGLRU: OK", res.stdout)

    def test_mglru_off(self) -> None:
        self.mglru("0")
        res = self.run_check()
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("MGLRU: FAIL (disabled)", res.stderr)

    def test_mglru_raw(self) -> None:
        self.mglru("abc")
        res = self.run_check()
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("unexpected value abc", res.stderr)

    def test_no_mglru(self) -> None:
        res = self.run_check()
        self.assertIn("lru_gen not exposed", res.stdout)


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
