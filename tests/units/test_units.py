# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS systemd 单元契约回归测试
#
# 每个用例在临时目录复制一份真实单元集合，再做单点变异，
# 验证校验器能够拒绝该类契约破坏。真实集合本身必须零违约。

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import units_lint  # noqa: E402

MACRO = "agentrt-macro-superv.service"
GATEWAY = "agentrt-gateway.service"
SCHED = "agentrt-sched.service"
TARGET = "agentrt.target"
INCIDENT = "airy-incident@.service"
HEALTH = "agentrt-health-check.service"


class UnitsCase(unittest.TestCase):
    """复制真实单元集合到临时目录后执行变异。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "units"
        shutil.copytree(ROOT / "units", self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def edit(self, name: str, old: str, new: str) -> None:
        path = self.root / name
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def errors(self) -> list[str]:
        return units_lint.check_dir(self.root)

    def assert_bad(self, needle: str) -> None:
        errs = self.errors()
        self.assertTrue(
            any(needle in err for err in errs),
            f"missing {needle!r} in {errs}",
        )


class CleanTreeCase(UnitsCase):
    """交付集合本身必须满足全部静态契约。"""

    def test_clean(self) -> None:
        self.assertEqual(self.errors(), [])


class SetCase(UnitsCase):
    """单元集合完整性。"""

    def test_missing(self) -> None:
        (self.root / SCHED).unlink()
        self.assert_bad("missing unit agentrt-sched.service")

    def test_extra_file(self) -> None:
        (self.root / "agentrt-ghost.service").write_text(
            "[Unit]\nDescription=ghost\n",
            encoding="utf-8",
        )
        self.assert_bad("unexpected unit file")


class FileCase(UnitsCase):
    """文件格式与权限。"""

    def test_mode(self) -> None:
        os.chmod(self.root / MACRO, 0o755)
        self.assert_bad("mode must be 0644")

    def test_crlf(self) -> None:
        path = self.root / MACRO
        text = path.read_text(encoding="utf-8")
        path.write_bytes(text.replace("\n", "\r\n").encode())
        self.assert_bad("CRLF line endings")

    def test_space(self) -> None:
        path = self.root / GATEWAY
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace("Type=notify", "Type=notify "),
            encoding="utf-8",
        )
        self.assert_bad("trailing whitespace")

    def test_dup_key(self) -> None:
        self.edit(
            MACRO,
            "WatchdogSec=30s\n",
            "WatchdogSec=30s\nWatchdogSec=30s\n",
        )
        self.assert_bad("already exists")


class ServiceCase(UnitsCase):
    """服务语义契约。"""

    def test_watchdog(self) -> None:
        self.edit(MACRO, "WatchdogSec=30s\n", "")
        self.assert_bad("WatchdogSec")

    def test_memory(self) -> None:
        self.edit("agentrt-mem.service", "MemoryMax=2G", "MemoryMax=1G")
        self.assert_bad("MemoryMax")

    def test_partof(self) -> None:
        self.edit(GATEWAY, "PartOf=agentrt.target\n", "")
        self.assert_bad("PartOf")

    def test_onfail(self) -> None:
        self.edit(GATEWAY, "OnFailure=airy-incident@%n.service\n", "")
        self.assert_bad("OnFailure")

    def test_ambient(self) -> None:
        self.edit(
            GATEWAY,
            "AmbientCapabilities=CAP_NET_BIND_SERVICE CAP_NET_RAW",
            "AmbientCapabilities=",
        )
        self.assert_bad("AmbientCapabilities")

    def test_cfg_type(self) -> None:
        self.edit("agentrt-config.service", "Type=oneshot", "Type=notify")
        self.assert_bad("Type")

    def test_wantedby(self) -> None:
        self.edit(
            GATEWAY,
            "WantedBy=agentrt.target",
            "WantedBy=multi-user.target",
        )
        self.assert_bad("WantedBy")


class DepsCase(UnitsCase):
    """依赖矩阵与无环约束。"""

    def test_req_order(self) -> None:
        self.edit(SCHED, "After=agentrt-gateway.service\n", "")
        self.assert_bad("Requires without After")

    def test_unknown(self) -> None:
        self.edit(
            "agentrt-cogn.service",
            "After=agentrt-gateway.service",
            "After=agentrt-gateway.service agentrt-ghost.service",
        )
        self.assert_bad("unknown unit agentrt-ghost.service")

    def test_cycle(self) -> None:
        self.edit(
            GATEWAY,
            "After=network-online.target",
            "After=network-online.target agentrt-sched.service",
        )
        self.assert_bad("dependency cycle")

    def test_target_req(self) -> None:
        self.edit(TARGET, " agentrt-mem.service", "")
        self.assert_bad("Requires set")


class TemplateCase(UnitsCase):
    """target 与 incident 模板契约。"""

    def test_dsl_target(self) -> None:
        self.edit(
            "agentrt-dsl.target",
            "Conflicts=agentrt.target\n",
            "",
        )
        self.assert_bad("Conflicts")

    def test_incident(self) -> None:
        self.edit(INCIDENT, "--status failed", "--status ok")
        self.assert_bad("ExecStart")


class HealthCase(UnitsCase):
    """启动自检单元契约。"""

    def test_hc_after(self) -> None:
        self.edit(HEALTH, "After=agentrt.target\n", "")
        self.assert_bad("After")

    def test_hc_wants(self) -> None:
        self.edit(HEALTH, "Wants=agentrt.target", "Requires=agentrt.target")
        self.assert_bad("unexpected directive")

    def test_hc_exec(self) -> None:
        self.edit(HEALTH, "health_check.sh", "health.sh")
        self.assert_bad("ExecStart")

    def test_hc_type(self) -> None:
        self.edit(HEALTH, "Type=oneshot", "Type=simple")
        self.assert_bad("Type")


if __name__ == "__main__":
    unittest.main()
