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
FIRSTBOOT = "agentrt-firstboot.service"
UPDATE_CHECK = "airymax-update-check.service"
UPDATE_TIMER = "airymax-update-check.timer"
LLM = "airy-llm.service"
AGENT_SLICE = "airymax-agent.slice"
DISK_MOUNT = "airy-disk-mount.service"
DISK_HEALTH = "airy-disk-health.service"
DISK_TIMER = "airy-disk-health.timer"
SESSION = "user.slice.d/50-airymax-memory.conf"
COEXIST = "airy-rt-coexist.service"
STAGE_ROW = "'7|桌面RT共存|10|desktoprt'"


class UnitsCase(unittest.TestCase):
    """复制真实单元集合到临时目录后执行变异。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "units"
        shutil.copytree(ROOT / "units", self.root)
        # 阶段契约表与单元同仓：校验器按 units/../scripts/ui.sh 读取，
        # 夹具必须还原同一布局，否则测的是一棵与交付形态不同的树。
        script = Path(self.tmp.name) / "scripts"
        script.mkdir()
        self.ui = script / "ui.sh"
        shutil.copy2(ROOT / "scripts" / "ui.sh", self.ui)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def edit(self, name: str, old: str, new: str) -> None:
        path = self.root / name
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def errors(self) -> list[str]:
        return units_lint.check_dir(self.root)

    def edit_ui(self, old: str, new: str) -> None:
        text = self.ui.read_text(encoding="utf-8")
        self.assertIn(old, text)
        self.ui.write_text(text.replace(old, new, 1), encoding="utf-8")

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


class StageNameCase(UnitsCase):
    """阶段名是单个参数，且必须与契约表逐字一致。"""

    def test_name_with_space(self) -> None:
        self.edit_ui(STAGE_ROW, "'7|桌面 RT 共存|10|desktoprt'")
        self.assert_bad("阶段名含空白")

    def test_name_unknown(self) -> None:
        self.edit(COEXIST, "桌面RT共存", "桌面RT")
        self.assert_bad("不在 ui.sh 契约表")

    def test_width_wrong(self) -> None:
        self.edit_ui(STAGE_ROW, "'7|桌面RT共存|12|desktoprt'")
        self.assert_bad("宽度 12 != 实际 10")

    def test_code_not_ascii(self) -> None:
        """console 代号非 ASCII 会让 VT 重新吐出乱码。"""
        self.edit_ui(STAGE_ROW, "'7|桌面RT共存|10|桌面'")
        self.assert_bad("不是纯 ASCII")

    def test_code_dup(self) -> None:
        self.edit_ui("'1|基础挂载|8|mount'", "'1|基础挂载|8|desktoprt'")
        self.assert_bad("console 代号重复")


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

    def test_startlimit_section(self) -> None:
        """限速键从 [Unit] 挪回 [Service] 必须被拒绝（B50 实测回退 10s）。"""
        self.edit(
            GATEWAY,
            "StartLimitBurst=3\nStartLimitIntervalSec=300\n",
            "",
        )
        self.edit(
            GATEWAY,
            "RestartSec=2s\n",
            "RestartSec=2s\nStartLimitBurst=3\nStartLimitIntervalSec=300\n",
        )
        self.assert_bad("unexpected directive [Service] StartLimitBurst")


class DepsCase(UnitsCase):
    """依赖矩阵与无环约束。"""

    def test_req_order(self) -> None:
        self.edit(
            SCHED,
            "After=agentrt-gateway.service agentrt-firstboot.service\n",
            "After=agentrt-firstboot.service\n",
        )
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

    def test_incident_guard(self) -> None:
        """缺 airyctl 时模板必须跳过，不能把一次失败放大成两条。"""
        self.edit(INCIDENT, "ConditionPathExists=/usr/sbin/airyctl\n", "")
        self.assert_bad("ConditionPathExists")


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


class FirstbootCase(UnitsCase):
    """首启初始化单元与门控契约。"""

    def test_fb_after(self) -> None:
        self.edit(FIRSTBOOT, "After=sysinit.target\n", "")
        self.assert_bad("After")

    def test_fb_before(self) -> None:
        self.edit(FIRSTBOOT, "Before=agentrt.target multi-user.target\n", "")
        self.assert_bad("Before")

    def test_fb_exec(self) -> None:
        self.edit(FIRSTBOOT, "firstboot.sh", "firstboot2.sh")
        self.assert_bad("ExecStart")

    def test_fb_type(self) -> None:
        self.edit(FIRSTBOOT, "Type=oneshot", "Type=simple")
        self.assert_bad("Type")

    def test_fb_dep(self) -> None:
        self.edit(GATEWAY, "Requires=agentrt-firstboot.service\n", "")
        self.assert_bad("Requires")


class UpdateCheckCase(UnitsCase):
    """升级检查单元契约。"""

    def test_uc_after(self) -> None:
        self.edit(UPDATE_CHECK, "After=network-online.target\n", "")
        self.assert_bad("After")

    def test_uc_type(self) -> None:
        self.edit(UPDATE_CHECK, "Type=oneshot", "Type=simple")
        self.assert_bad("Type")

    def test_uc_exec(self) -> None:
        self.edit(
            UPDATE_CHECK,
            "/usr/bin/airy-update check",
            "/usr/bin/airy-update status",
        )
        self.assert_bad("ExecStart")

    def test_uc_exit(self) -> None:
        """10 是「有新版」、2 是「没连上」：都不是失败，漏掉任一都会报红。"""
        self.edit(UPDATE_CHECK, "SuccessExitStatus=10 2\n", "")
        self.assert_bad("SuccessExitStatus")

    def test_uc_state(self) -> None:
        self.edit(UPDATE_CHECK, "StateDirectory=airy-update\n", "")
        self.assert_bad("StateDirectory")


class LlmCase(UnitsCase):
    """推理单元契约。

    推理进程由 execv 换成引擎本体，没有 sd_notify，故 Type 只能是
    simple；配置错误以 78 退出时必须停下等运维处理，重启策略不能是
    always，否则一个坏清单会让单元每 5 秒重试到天荒地老。
    """

    def test_llm_exec(self) -> None:
        self.edit(LLM, "/usr/bin/airy-model serve", "/usr/bin/airy-model info")
        self.assert_bad("ExecStart")

    def test_llm_type(self) -> None:
        self.edit(LLM, "Type=simple", "Type=notify")
        self.assert_bad("Type")

    def test_llm_restart(self) -> None:
        self.edit(LLM, "Restart=on-failure", "Restart=always")
        self.assert_bad("Restart")

    def test_llm_state_mode(self) -> None:
        """运行根同时放模型资产，组外用户不应可读。"""
        self.edit(LLM, "StateDirectoryMode=0750", "StateDirectoryMode=0755")
        self.assert_bad("StateDirectoryMode")

    def test_llm_devices(self) -> None:
        """PrivateDevices 会藏掉 GPU 设备节点，必须被拒。"""
        self.edit(
            LLM,
            "PrivateTmp=yes\n",
            "PrivateTmp=yes\nPrivateDevices=yes\n",
        )
        self.assert_bad("PrivateDevices")

    def test_llm_order(self) -> None:
        self.edit(LLM, "After=agentrt-firstboot.service\n", "")
        self.assert_bad("Requires without After")

    def test_llm_manifest(self) -> None:
        """缺清单门控时单元会在首启完成前反复拉起重启。"""
        self.edit(
            LLM,
            "ConditionPathExists=/var/lib/airy-llm/manifest.json\n",
            "",
        )
        self.assert_bad("ConditionPathExists")

    def test_llm_caps(self) -> None:
        self.edit(
            LLM,
            "CapabilityBoundingSet=\n",
            "CapabilityBoundingSet=CAP_NET_BIND_SERVICE\n",
        )
        self.assert_bad("CapabilityBoundingSet")

    def test_llm_refusal_not_retried(self) -> None:
        """准入拒绝与资产缺失是确定结论，不能被重启策略反复放大。"""
        self.edit(LLM, "RestartPreventExitStatus=75 78\n", "")
        self.assert_bad("RestartPreventExitStatus")


class SliceCase(UnitsCase):
    """推理负载的内存边界契约。

    285H 现场三个实例把 RSS 推到 29.5G / 30G 后整机进入 direct reclaim，
    桌面与 SSH 一起失去响应。边界因此必须存在于 slice 上：推理单元只声明
    归属，重复声明会让「哪一层在生效」无从判断。
    """

    def test_slice_missing(self) -> None:
        (self.root / AGENT_SLICE).unlink()
        self.assert_bad(f"missing unit {AGENT_SLICE}")

    def test_slice_max(self) -> None:
        """硬上限抬到整机规模等于没有边界。"""
        self.edit(AGENT_SLICE, "MemoryMax=90%", "MemoryMax=99%")
        self.assert_bad("MemoryMax")

    def test_slice_swap(self) -> None:
        """换页上限放宽到内存上限之外，超额页又会转进 swap。"""
        self.edit(AGENT_SLICE, "MemorySwapMax=10%", "MemorySwapMax=90%")
        self.assert_bad("MemorySwapMax")

    def test_slice_v2_only(self) -> None:
        """MemoryHigh 在 v1 hybrid 上不落地，不得回流。"""
        self.edit(AGENT_SLICE, "TasksMax=80%\n",
                  "TasksMax=80%\nMemoryHigh=75%\n")
        self.assert_bad("unexpected directive")

    def test_llm_membership(self) -> None:
        self.edit(LLM, f"Slice={AGENT_SLICE}\n", "")
        self.assert_bad("Slice")

    def test_llm_dup_limit(self) -> None:
        """上限只能声明一次：单元与 slice 同时声明必然分叉。"""
        self.edit(LLM, "TasksMax=512\n", "TasksMax=512\nMemoryMax=90%\n")
        self.assert_bad("unexpected directive")


class DropinCase(UnitsCase):
    """会话池的内存边界契约。

    285H 上 AirymaxRT 在自己的会话里直接 exec 推理进程，airymax-agent.slice
    的上限对它们无效；user.slice 是所有会话 scope 的共同父节点，边界必须
    落在这一层，任何 spawn 路径都逃不出去。
    """

    def test_dropin_missing(self) -> None:
        (self.root / SESSION).unlink()
        self.assert_bad(f"missing drop-in {SESSION}")

    def test_dropin_orphan(self) -> None:
        (self.root / "user.slice.d/90-extra.conf").write_text(
            "[Slice]\nMemoryMax=95%\n",
            encoding="utf-8",
        )
        self.assert_bad("unexpected drop-in file")

    def test_dropin_max(self) -> None:
        """会话池抬到整机规模等于没有边界。"""
        self.edit(SESSION, "MemoryMax=85%", "MemoryMax=99%")
        self.assert_bad("MemoryMax")

    def test_dropin_swap(self) -> None:
        """会话池的换页上限同样只能收紧，不能放宽。"""
        self.edit(SESSION, "MemorySwapMax=15%", "MemorySwapMax=85%")
        self.assert_bad("MemorySwapMax")

    def test_dropin_v2_only(self) -> None:
        """MemoryHigh 在 v1 hybrid 上不落地，不得混进会话池。"""
        self.edit(
            SESSION,
            "MemoryMax=85%\n",
            "MemoryMax=85%\nMemoryHigh=70%\n",
        )
        self.assert_bad("unexpected directive")


class DiskCase(UnitsCase):
    """数据分区挂载与健康单元契约。

    挂载单元不能加固：ProtectSystem/PrivateTmp 会把 mount 关进私有
    命名空间，挂载结果对系统其余部分不可见。健康单元靠挂载点条件
    跳过未选数据盘的机器，否则每个启动周期都会刷一条 incident。
    """

    def test_mount_after(self) -> None:
        self.edit(DISK_MOUNT, "After=local-fs.target\n", "")
        self.assert_bad("After")

    def test_mount_before(self) -> None:
        self.edit(
            DISK_MOUNT,
            "Before=agentrt.target multi-user.target\n",
            "",
        )
        self.assert_bad("Before")

    def test_mount_exec(self) -> None:
        self.edit(DISK_MOUNT, "airy-disk mount", "airy-disk status")
        self.assert_bad("ExecStart")

    def test_mount_type(self) -> None:
        self.edit(DISK_MOUNT, "Type=oneshot", "Type=simple")
        self.assert_bad("Type")

    def test_mount_harden(self) -> None:
        self.edit(
            DISK_MOUNT,
            "TimeoutStartSec=120\n",
            "TimeoutStartSec=120\nPrivateTmp=yes\n",
        )
        self.assert_bad("unexpected directive")

    def test_health_after(self) -> None:
        self.edit(DISK_HEALTH, "After=airy-disk-mount.service\n", "")
        self.assert_bad("After")

    def test_health_guard(self) -> None:
        self.edit(
            DISK_HEALTH,
            "ConditionPathIsMountPoint=/data\n",
            "",
        )
        self.assert_bad("ConditionPathIsMountPoint")

    def test_health_exec(self) -> None:
        self.edit(DISK_HEALTH, "airy-disk health", "airy-disk mount")
        self.assert_bad("ExecStart")

    def test_timer_calendar(self) -> None:
        self.edit(DISK_TIMER, "OnCalendar=daily", "OnCalendar=hourly")
        self.assert_bad("OnCalendar")

    def test_timer_persistent(self) -> None:
        self.edit(DISK_TIMER, "Persistent=true", "Persistent=false")
        self.assert_bad("Persistent")


class TimerCase(UnitsCase):
    """定时器单元契约。"""

    def test_calendar(self) -> None:
        self.edit(UPDATE_TIMER, "OnCalendar=daily", "OnCalendar=hourly")
        self.assert_bad("OnCalendar")

    def test_delay(self) -> None:
        self.edit(UPDATE_TIMER, "RandomizedDelaySec=6h\n", "")
        self.assert_bad("RandomizedDelaySec")

    def test_persistent(self) -> None:
        self.edit(UPDATE_TIMER, "Persistent=true", "Persistent=false")
        self.assert_bad("Persistent")

    def test_wantedby(self) -> None:
        self.edit(
            UPDATE_TIMER,
            "WantedBy=timers.target",
            "WantedBy=multi-user.target",
        )
        self.assert_bad("WantedBy")

    def test_unit_key(self) -> None:
        """绑定靠同名约定，显式 Unit= 会把两份事实拆到两处。"""
        self.edit(
            UPDATE_TIMER,
            "Persistent=true",
            "Unit=agentrt.target\nPersistent=true",
        )
        self.assert_bad("unexpected directive [Timer] Unit")


if __name__ == "__main__":
    unittest.main()
