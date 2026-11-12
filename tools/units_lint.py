# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS systemd 单元契约校验器
#
# 本文件对 units/ 目录执行静态契约校验，覆盖单元集合、依赖
# 矩阵、资源配额、重启策略、加固与日志语义。校验不依赖
# systemd 运行时，可在非特权宿主与 CI 中直接执行。

"""AirymaxOS systemd unit contract checker.

设计依据为 docs-linux/100-operations/07-systemd-integration.md。
daemon 间启动顺序以该文档 §7.2 依赖矩阵为唯一事实来源。
"""

from __future__ import annotations

import logging
import re
import sys
import unicodedata
from configparser import ConfigParser
from configparser import Error as IniError
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "AGENT_SLICE",
    "COEXIST",
    "DROPINS",
    "FIRSTBOOT",
    "SERVICES",
    "SESSION_DROPIN",
    "TARGETS",
    "TEMPLATES",
    "TIMERS",
    "UNITS",
    "check_dir",
    "check_file",
    "check_script",
    "main",
]

LOG = logging.getLogger("units_lint")

ROOT = Path(__file__).resolve().parent.parent
UNIT_DIR = ROOT / "units"
SHEBANG = "#!/usr/bin/env bash"

SYSTEM_UNITS = frozenset({
    "display-manager.service",
    "graphical.target",
    "local-fs.target",
    "multi-user.target",
    "NetworkManager.service",
    "network-online.target",
    "rtkit-daemon.service",
    "slices.target",
    "sysinit.target",
    "systemd-journald.service",
    "systemd-journald.socket",
    "user@.service",
})

CAP_BIND = "CAP_NET_BIND_SERVICE CAP_NET_RAW"
CAP_DEV = "CAP_SYS_ADMIN CAP_MKNOD"
CAP_SCHED = "CAP_SYS_NICE CAP_SYS_RESOURCE"
CAP_MACRO = "CAP_SYS_PTRACE CAP_KILL CAP_SETUID CAP_SETGID"
CAP_MACRO_AMB = "CAP_SYS_PTRACE CAP_KILL"
CAP_NET = "CAP_NET_ADMIN CAP_NET_RAW"
CAP_SEC = "CAP_MAC_ADMIN CAP_SYSLOG CAP_SETUID"
CAP_VFS = "CAP_DAC_OVERRIDE"


@dataclass(frozen=True)
class Service:
    """单个 daemon 单元的期望契约。"""

    daemon: str
    after: str
    restart: str
    restart_sec: str
    burst: str
    mem_max: str
    mem_high: str
    cpu: str
    tasks: str
    groups: str
    caps: str = ""
    ambient: str = ""
    rw: str = ""
    requires: str = ""
    wants: str = ""
    nofile: str = "65536"
    io_class: str = "best-effort"
    io_prio: str = "3"
    exec_start: str = ""
    omit: tuple[tuple[str, str], ...] = ()
    extra: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True)
class Target:
    """target 单元的期望聚合集合。"""

    requires: tuple[str, ...]
    after: str = ""
    conflicts: str = ""


SERVICES: dict[str, Service] = {
    "agentrt-gateway.service": Service(
        daemon="gateway",
        after="network-online.target",
        restart="always",
        restart_sec="2s",
        burst="3",
        mem_max="1G",
        mem_high="800M",
        cpu="200%",
        tasks="1024",
        groups="airy-net",
        caps=CAP_BIND,
        ambient=CAP_BIND,
        rw="/var/lib/agentrt/gateway",
        io_prio="3",
    ),
    "agentrt-cogn.service": Service(
        daemon="cogn",
        after="agentrt-gateway.service",
        requires="agentrt-gateway.service",
        restart="always",
        restart_sec="5s",
        burst="5",
        mem_max="4G",
        mem_high="3.2G",
        cpu="400%",
        tasks="256",
        groups="airy-cogn",
        rw="/var/lib/agentrt/cogn",
        io_prio="3",
    ),
    "agentrt-dev.service": Service(
        daemon="dev",
        after="agentrt-gateway.service",
        requires="agentrt-gateway.service",
        restart="on-failure",
        restart_sec="5s",
        burst="3",
        mem_max="256M",
        mem_high="200M",
        cpu="50%",
        tasks="128",
        groups="airy-dev",
        caps=CAP_DEV,
        ambient=CAP_DEV,
        rw="/var/lib/agentrt/devices /dev",
        io_prio="4",
    ),
    "agentrt-sched.service": Service(
        daemon="sched",
        after="agentrt-gateway.service",
        requires="agentrt-gateway.service",
        restart="always",
        restart_sec="1s",
        burst="2",
        mem_max="256M",
        mem_high="200M",
        cpu="100%",
        tasks="128",
        groups="airy-sched",
        caps=CAP_SCHED,
        ambient=CAP_SCHED,
        rw="/var/lib/agentrt/sched",
        io_class="realtime",
        io_prio="3",
    ),
    "agentrt-macro-superv.service": Service(
        daemon="macro",
        after="systemd-journald.service agentrt-sched.service",
        requires="systemd-journald.service agentrt-sched.service",
        wants="agentrt-gateway.service",
        restart="always",
        restart_sec="1s",
        burst="3",
        mem_max="512M",
        mem_high="400M",
        cpu="200%",
        tasks="512",
        groups="airy-monitor airy-alert airy-backup",
        caps=CAP_MACRO,
        ambient=CAP_MACRO_AMB,
        io_class="realtime",
        io_prio="4",
        extra=(
            ("Service", "LimitNPROC", "512"),
            ("Service", "PrivateDevices", "yes"),
            ("Service", "KillSignal", "SIGTERM"),
            ("Service", "SendSIGKILL", "yes"),
        ),
    ),
    "agentrt-audit.service": Service(
        daemon="audit",
        after="agentrt-sched.service",
        requires="agentrt-sched.service",
        wants="agentrt-gateway.service",
        restart="always",
        restart_sec="2s",
        burst="3",
        mem_max="1G",
        mem_high="800M",
        cpu="200%",
        tasks="512",
        groups="airy-monitor airy-backup",
        rw=(
            "/var/lib/agentrt/monitor /var/lib/agentrt/incidents"
            " /var/lib/agentrt/backup /var/lib/agentrt/alerts"
        ),
        io_prio="2",
        extra=(
            ("Service", "ExecStartPost", "/usr/sbin/audit_d --restore"),
        ),
    ),
    "agentrt-net.service": Service(
        daemon="net",
        after="agentrt-gateway.service agentrt-sched.service",
        requires="agentrt-gateway.service",
        wants="agentrt-sched.service",
        restart="always",
        restart_sec="2s",
        burst="3",
        mem_max="1G",
        mem_high="800M",
        cpu="200%",
        tasks="1024",
        groups="airy-net",
        caps=CAP_NET,
        ambient=CAP_NET,
        rw="/var/lib/agentrt/net",
        io_prio="2",
    ),
    "agentrt-mem.service": Service(
        daemon="mem",
        after="agentrt-gateway.service agentrt-sched.service",
        requires="agentrt-gateway.service",
        wants="agentrt-sched.service",
        restart="always",
        restart_sec="2s",
        burst="3",
        mem_max="2G",
        mem_high="1600M",
        cpu="100%",
        tasks="256",
        groups="airy-memory",
        rw="/var/lib/agentrt/memory /var/lib/agentrt/agents",
        io_prio="3",
    ),
    "agentrt-sec.service": Service(
        daemon="sec",
        after="agentrt-audit.service",
        requires="agentrt-audit.service",
        restart="always",
        restart_sec="1s",
        burst="2",
        mem_max="512M",
        mem_high="400M",
        cpu="100%",
        tasks="256",
        groups="airy-security airy-alert",
        caps=CAP_SEC,
        ambient=CAP_SEC,
        rw=(
            "/etc/agentrt/keys /var/log/agentrt"
            " /var/lib/agentrt/security"
        ),
        io_class="realtime",
        io_prio="1",
        extra=(
            ("Service", "ExecStartPre", "/sbin/modprobe airy_lsm"),
            ("Service", "Nice", "-10"),
            ("Service", "ProtectSystem", "no"),
        ),
    ),
    "agentrt-logger.service": Service(
        daemon="logger",
        after="agentrt-audit.service",
        requires="agentrt-audit.service",
        restart="always",
        restart_sec="1s",
        burst="5",
        mem_max="1G",
        mem_high="800M",
        cpu="100%",
        tasks="256",
        groups="airy-alert airy-log",
        rw="/var/log/agentrt /var/lib/agentrt/log",
        nofile="131072",
        io_prio="2",
        extra=(
            ("Service", "TimeoutStartSec", "20"),
            ("Service", "TimeoutStopSec", "10"),
            ("Service", "KillMode", "control-group"),
        ),
    ),
    "agentrt-vfs.service": Service(
        daemon="vfs",
        after="agentrt-net.service agentrt-sec.service",
        wants="agentrt-net.service agentrt-sec.service",
        restart="always",
        restart_sec="2s",
        burst="3",
        mem_max="1G",
        mem_high="800M",
        cpu="100%",
        tasks="512",
        groups="airy-vfs",
        caps=CAP_VFS,
        ambient=CAP_VFS,
        rw="/var/lib/agentrt/vfs /var/lib/agentrt/agents",
        io_prio="2",
    ),
    "agentrt-config.service": Service(
        daemon="config",
        after="agentrt-vfs.service",
        wants="agentrt-vfs.service",
        restart="on-failure",
        restart_sec="5s",
        burst="3",
        mem_max="256M",
        mem_high="200M",
        cpu="50%",
        tasks="128",
        groups="airy-config",
        rw="/etc/agentrt /var/lib/agentrt/config",
        io_prio="4",
        exec_start=(
            "/usr/sbin/config_d --load --config=/etc/agentrt/airy.conf"
        ),
        omit=(
            ("Service", "NotifyAccess"),
            ("Service", "WatchdogSec"),
            ("Service", "KillMode"),
        ),
        extra=(
            ("Service", "Type", "oneshot"),
            ("Service", "RemainAfterExit", "yes"),
            ("Service", "ExecStop", "/bin/true"),
            ("Service", "ExecReload", "/usr/sbin/config_d --reload"),
        ),
    ),
}

FIRSTBOOT = "agentrt-firstboot.service"

TARGETS: dict[str, Target] = {
    "agentrt.target": Target(
        requires=("sysinit.target",) + tuple(SERVICES) + (FIRSTBOOT,),
        after="sysinit.target network-online.target",
    ),
    "agentrt-dsl.target": Target(
        requires=(
            "agentrt-macro-superv.service",
            "agentrt-logger.service",
            "agentrt-config.service",
            "agentrt-sec.service",
            "agentrt-audit.service",
            "agentrt-mem.service",
        ),
        conflicts="agentrt.target",
    ),
}

# 失败处理模板必须自己先能起来。285H 现场：数据盘挂载失败触发本模板，
# 本模板因 airyctl 未装、airy 账户不存在而失败，于是「一次失败」变成
# 「两条 failed 单元」，桌面就绪探针再据此判整机未就绪。0.0.4 只交付
# 单元接口面，airyctl 与账户属于 agentrt 载荷，尚未进入本版镜像；缺载荷
# 时报跳过而不是失败，原始失败单元仍在 systemctl --failed 里可见。
TEMPLATES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "airy-incident@.service": (
        ("Unit", "Description", "Handle failure of %i"),
        ("Unit", "After", "%i"),
        ("Unit", "ConditionPathExists", "/usr/sbin/airyctl"),
        ("Service", "Type", "oneshot"),
        ("Service", "TimeoutStartSec", "20"),
        ("Service", "TimeoutStopSec", "10"),
        (
            "Service",
            "ExecStart",
            "/usr/sbin/airyctl incident create --source systemd"
            " --unit %i --status failed",
        ),
        ("Service", "User", "airy"),
        ("Service", "Group", "airy"),
    ),
}

HEALTH = "agentrt-health-check.service"

HEALTH_PAIRS: dict[tuple[str, str], str] = {
    ("Unit", "After"): "agentrt.target",
    ("Unit", "Wants"): "agentrt.target",
    ("Service", "Type"): "oneshot",
    (
        "Service",
        "ExecStart",
    ): "/usr/lib/airymaxos/scripts/health_check.sh",
    ("Service", "RemainAfterExit"): "yes",
    ("Service", "TimeoutStartSec"): "120",
    ("Install", "WantedBy"): "multi-user.target",
}

SCRIPT = ROOT / "scripts" / "health_check.sh"

FIRSTBOOT_PAIRS: dict[tuple[str, str], str] = {
    ("Unit", "After"): "sysinit.target",
    ("Unit", "Before"): "agentrt.target multi-user.target",
    ("Service", "Type"): "oneshot",
    ("Service", "ExecStart"): (
        "/usr/lib/airymaxos/scripts/firstboot.sh"
    ),
    ("Service", "RemainAfterExit"): "yes",
    ("Service", "TimeoutStartSec"): "300",
    ("Service", "TimeoutStopSec"): "30",
    ("Install", "WantedBy"): "multi-user.target",
}

FIRSTBOOT_SCRIPT = ROOT / "scripts" / "firstboot.sh"

BOOT_MOUNT = "airy-boot-mount.service"

# 基础挂载是九阶段的第一段，必须排在数据分区之前：数据分区单元在 /data
# 下创建并挂载文件系统，根若仍为只读，它报出的失败会被误读成数据盘故障。
# 只读根是救援与恢复启动的正常形态，退出码 3 因此声明为成功，由阶段行
# 以 note 报出；真正的后果由后续写盘阶段自己冒出来。
BOOT_MOUNT_PAIRS: dict[tuple[str, str], str] = {
    ("Unit", "After"): "local-fs.target",
    ("Unit", "Before"): (
        "airy-disk-mount.service agentrt.target multi-user.target"
    ),
    ("Unit", "OnFailure"): "airy-incident@%n.service",
    ("Service", "Type"): "oneshot",
    ("Service", "ExecStart"): (
        "/usr/lib/airymaxos/scripts/stage_run.sh 基础挂载 3 root,boot,efi"
        " /usr/lib/airymaxos/scripts/boot_mount.sh"
    ),
    ("Service", "SuccessExitStatus"): "3",
    ("Service", "RemainAfterExit"): "yes",
    # 心跳 10 秒一次、预期上限与单元超时同为 30 秒。默认的 30/60 在这里
    # 全错：周期等于超时意味着一行 wait 都来不及落就被 systemd 杀掉，而
    # 打印的上限还会声称 60 秒——现场看到的时间与实际截断对不上。
    ("Service", "Environment"): "AIRY_STAGE_BEAT=10 AIRY_STAGE_LIMIT=30",
    ("Service", "TimeoutStartSec"): "30",
    ("Install", "WantedBy"): "multi-user.target",
}

BOOT_MOUNT_SCRIPT = ROOT / "scripts" / "boot_mount.sh"

BOOT_WATCH = "airy-boot-watch.service"

# 启动卡点自证：唯一一个刻意不参与启动排序的单元。挂在目标之后就要等目标
# 达成，而它存在的理由正是目标达不成；由 timer 的 OnBootSec 触发，与
# 启动排序完全解耦。DefaultDependencies=no 不是风格问题：服务类单元的
# 缺省依赖自带 Requires/After=sysinit.target，只删显式那一行仍排在目标
# 之后，看门狗在最需要它的场景里拿不到执行机会（285H 2026-11-09 冷启动
# 被 plymouth-read-write 卡死，本单元 journal 为空）。stdout 只进 journal，
# console 与串口由脚本按设备实际归属自己写，交给 systemd 复制一遍会在
# /dev/console 归串口的机器上重复。
BOOT_WATCH_PAIRS: dict[tuple[str, str], str] = {
    ("Unit", "DefaultDependencies"): "no",
    ("Unit", "After"): "systemd-journald.socket",
    ("Service", "Type"): "oneshot",
    (
        "Service",
        "ExecStart",
    ): "/usr/lib/airymaxos/scripts/boot_watch.sh",
    ("Service", "TimeoutStartSec"): "120",
    ("Service", "StandardOutput"): "journal",
    ("Service", "StandardError"): "journal",
}

BOOT_WATCH_SCRIPT = ROOT / "scripts" / "boot_watch.sh"

NET_DOCTOR = "airymax-net-doctor.service"

# 首启网络自检：只读、只判一次，结论落 /run/airymaxos/net.state 并进
# journal。ExecStart 前的 `-` 是刻意的：判出失败态时脚本返回 3，若让
# systemd 记成 failed，`systemctl --failed` 就多出一条二手失败，掩盖真正
# 的失败单元；结论本身已经是 /run 里的那一行。Before=graphical.target 让
# 结论先于桌面出现，After=NetworkManager 保证 nmcli 可用时 NetworkManager
# 已经在位。ProtectSystem=strict 下 /run 只读，RuntimeDirectory 是唯一
# 可写落点。
NET_DOCTOR_PAIRS: dict[tuple[str, str], str] = {
    ("Unit", "Description"): "Report the first-boot network verdict to the journal",
    ("Unit", "After"): "NetworkManager.service",
    ("Unit", "Before"): "graphical.target",
    ("Service", "Type"): "oneshot",
    (
        "Service",
        "ExecStart",
    ): "-/usr/lib/airymaxos/scripts/net_doctor.sh",
    ("Service", "RemainAfterExit"): "yes",
    ("Service", "RuntimeDirectory"): "airymaxos",
    ("Service", "RuntimeDirectoryMode"): "0755",
    ("Service", "TimeoutStartSec"): "30",
    ("Service", "ProtectSystem"): "strict",
    ("Service", "ProtectHome"): "yes",
    ("Service", "PrivateTmp"): "yes",
    ("Service", "NoNewPrivileges"): "yes",
    ("Service", "StandardOutput"): "journal",
    ("Service", "StandardError"): "journal",
    ("Install", "WantedBy"): "multi-user.target",
}

NET_DOCTOR_SCRIPT = ROOT / "scripts" / "net_doctor.sh"

DISK_MOUNT = "airy-disk-mount.service"

# 挂载单元必须运行在宿主挂载命名空间里：ProtectSystem/PrivateTmp 之类
# 加固会把它关进私有命名空间，mount 的结果对其它进程不可见。/data 承载
# 记忆与虚拟文件系统状态，所以它排在 agentrt.target 之前；OnFailure 把
# 挂载失败交给 incident 通道，而不是让服务在缺数据盘的情况下静默起跑。
DISK_MOUNT_PAIRS: dict[tuple[str, str], str] = {
    ("Unit", "After"): "local-fs.target",
    ("Unit", "Before"): "agentrt.target multi-user.target",
    ("Unit", "OnFailure"): "airy-incident@%n.service",
    ("Service", "Type"): "oneshot",
    ("Service", "ExecStart"): (
        "/usr/lib/airymaxos/scripts/stage_run.sh"
        " 数据分区 4 - /usr/bin/airy-disk mount"
    ),
    # 4 是「本机未启用数据分区」：0.0.3 及更早装成的系统没有 /data，缺记录
    # 不是故障，与 airy-disk-health 的 ConditionPathIsMountPoint 同一口径。
    ("Service", "SuccessExitStatus"): "4",
    ("Service", "RemainAfterExit"): "yes",
    # 数据分区在慢盘上真的会跑满两分钟，心跳用默认 30 秒，上限必须跟着
    # 单元超时走到 120 秒；沿用默认 60 会在大容量盘上提前宣告超时。
    ("Service", "Environment"): "AIRY_STAGE_LIMIT=120",
    ("Service", "TimeoutStartSec"): "120",
    ("Install", "WantedBy"): "multi-user.target",
}

DISK_HEALTH = "airy-disk-health.service"

# 没有 /data 挂载点时直接跳过：装机未选数据盘不是故障，让单元在此处
# 失败只会在每个启动周期刷一条 incident，掩盖真正的挂载失败。健康检查
# 没有 [Install] 段，只由 airy-disk-health.timer 拉起。
DISK_HEALTH_PAIRS: dict[tuple[str, str], str] = {
    ("Unit", "After"): "airy-disk-mount.service",
    ("Unit", "ConditionPathIsMountPoint"): "/data",
    ("Unit", "OnFailure"): "airy-incident@%n.service",
    ("Service", "Type"): "oneshot",
    ("Service", "ExecStart"): "/usr/bin/airy-disk health",
    ("Service", "TimeoutStartSec"): "120",
}

RT_BANDWIDTH = "airy-rt-bandwidth.service"

# 实时预算必须在 rtkit-daemon 与显示管理器之前落地：两者启动时就会
# 尝试 SCHED_RR/FIFO，而 RT_GROUP_SCHED 下新建组的 cpu.rt_runtime_us
# 默认是 0，晚一步就会让音频与合成器永久退化成非实时。先等
# slices.target，免得在 cgroupfs 里抢在 systemd 之前建出 slice 目录；
# 尚未实例化的 slice 由本单元创建，systemd 之后接管同一目录也不会重置
# cpu.rt_runtime_us——它不管理这个文件。退出码 1/3 都按失败上报：部分
# 落地同样是故障，交给 incident 通道而不是降级成一条日志。
#
# 预算在开机时必然只落 3/4 个 slice（airymax-desktop.slice 登录后才存在），
# 因此退出码 3 是剖面事实而不是故障，声明为成功；门禁仍按退出码区分。
#
# Before=user@.service：登录会话的 slice 由 user@ 启动时才创建，晚于本单元
# 就只能等 seed 补写。排在它之前，新建组至少在本次开机内还来得及被补上。
RT_PAIRS: dict[tuple[str, str], str] = {
    ("Unit", "After"): "slices.target",
    ("Unit", "Before"): (
        "agentrt.target rtkit-daemon.service user@.service"
        " display-manager.service"
    ),
    ("Unit", "OnFailure"): "airy-incident@%n.service",
    ("Service", "Type"): "oneshot",
    ("Service", "ExecStart"): (
        "/usr/lib/airymaxos/scripts/stage_run.sh 运行时带宽 3 state,applied"
        " /usr/bin/airy-rt-cap apply"
    ),
    ("Service", "SuccessExitStatus"): "3",
    ("Service", "RemainAfterExit"): "yes",
    ("Service", "TimeoutStartSec"): "60",
    ("Install", "WantedBy"): "multi-user.target",
}

MEMCAP = "airy-rt-memcap.service"

# 换页上限必须是独立单元，不能挂在带宽单元的 ExecStartPost 上：systemd
# 只在 ExecStart 成功后执行 ExecStartPost，而带宽预算在开机时只落 3/4 个
# slice，一旦把部分落地判成失败，memsw 就永远停在 infinity（285H 实证）。
# 上限只依赖 slice 目录，开机即可完整落地；hybrid 层级上 systemd 只记录
# MemorySwapMax 属性而不写 memory.memsw.limit_in_bytes，只能由 memcap 补。
# 写入不跨 daemon-reload：systemd 在 v1 上每次 reload 都把它写回 infinity，
# 所以配 airy-rt-memcap.timer 周期重写；未设 RemainAfterExit，timer 的
# OnUnitInactiveSec 才有下一次触发。
MEMCAP_PAIRS: dict[tuple[str, str], str] = {
    ("Unit", "After"): "slices.target",
    ("Unit", "Before"): "agentrt.target display-manager.service",
    ("Unit", "OnFailure"): "airy-incident@%n.service",
    ("Unit", "ConditionPathExists"): "/usr/bin/airy-rt-cap",
    ("Service", "Type"): "oneshot",
    ("Service", "ExecStart"): (
        "/usr/lib/airymaxos/scripts/stage_run.sh 换页上限 3 layout,steps"
        " /usr/bin/airy-rt-cap memcap --apply"
    ),
    ("Service", "SuccessExitStatus"): "3",
    ("Service", "TimeoutStartSec"): "60",
    ("Service", "RuntimeDirectory"): "airymax-ui",
    ("Service", "RuntimeDirectoryPreserve"): "yes",
    ("Service", "ProtectSystem"): "strict",
    ("Service", "ProtectHome"): "yes",
    ("Service", "PrivateTmp"): "yes",
    ("Service", "NoNewPrivileges"): "yes",
    ("Install", "WantedBy"): "multi-user.target",
}

POWER = "airy-power.service"

# 电源档位必须在桌面会话与 agentrt 栈启动之前落地：两者一起负载就把热区
# 推到 critical 附近，等 timer 在 120 秒后才纠正时已节流过一轮。单元不设
# RemainAfterExit——oneshot 退出后回到 inactive，timer 的 OnUnitInactiveSec
# 才能继续排下一次；设成 active (exited) 会让周期重评永不触发。退出码 3
# 表示「已降档但机器仍热」，按成功上报，只有真正的写入失败才交 incident。
# ProtectSystem=strict 不覆盖 /dev、/proc、/sys，sysfs 写入照常。节流采样
# 落在 /run/airy-power，oneshot 每次退出都会回收运行时目录；Preserve=yes
# 才能把上一次的计数留到下一次，否则每轮都当作首次采样，永远算不出增速。
#
# ExecStart 一律经由 stage_run.sh 而不是工具本体：阶段结论行必须与九阶段
# 契约同源，而各工具是 Python 入口、无法 source ui.sh。运行器只做取值与
# 分档，阶段名、序号与列宽仍由 ui.sh 的表持有；一次开机只打一行由
# ui_gate 保证，标记落 tmpfs。参数依次是阶段名、按 note 上报的退出码、
# 要搬进阶段行的键，其余原样交给工具。
POWER_PAIRS: dict[tuple[str, str], str] = {
    ("Unit", "After"): "sysinit.target",
    ("Unit", "Before"): "agentrt.target display-manager.service",
    ("Unit", "OnFailure"): "airy-incident@%n.service",
    ("Service", "Type"): "oneshot",
    ("Service", "ExecStart"): (
        "/usr/lib/airymaxos/scripts/stage_run.sh 电源与热治理 3"
        " landed,cap,severity,charge /usr/bin/airy-power apply"
    ),
    ("Service", "SuccessExitStatus"): "3",
    ("Service", "TimeoutStartSec"): "60",
    ("Service", "RuntimeDirectory"): "airy-power airymax-ui",
    ("Service", "RuntimeDirectoryPreserve"): "yes",
    ("Service", "ProtectSystem"): "strict",
    ("Service", "ProtectHome"): "yes",
    ("Service", "PrivateTmp"): "yes",
    ("Service", "NoNewPrivileges"): "yes",
    ("Install", "WantedBy"): "multi-user.target",
}

COEXIST = "airy-rt-coexist.service"

# 共存单元只经 D-Bus 让 systemd 改一个运行时属性，不直接写 cgroupfs：
# --runtime 的语义只有 systemd 能保证，自己写文件会在单元重载后被冲掉。
# 服务在桌面 slice 未加载时以 0 退出，因此不挂 incident——无桌面机器
# 每一轮都会走到这条路径，挂上只会把正常状态记成故障。ProtectSystem
# 不拦 D-Bus 套接字；单元本身是 oneshot，不设 RemainAfterExit，定时器
# 的 OnUnitInactiveSec 才有下一次触发。
COEXIST_PAIRS: dict[tuple[str, str], str] = {
    ("Unit", "After"): "sysinit.target airy-rt-bandwidth.service",
    ("Unit", "Before"): "display-manager.service",
    ("Unit", "ConditionPathExists"): "/usr/bin/airy-rt-cap",
    ("Service", "Type"): "oneshot",
    ("Service", "ExecStart"): (
        "/usr/lib/airymaxos/scripts/stage_run.sh 桌面RT共存 -"
        " mode,weight,current,applied /usr/bin/airy-rt-cap coexist --apply"
    ),
    ("Service", "TimeoutStartSec"): "60",
    ("Service", "RuntimeDirectory"): "airymax-ui",
    ("Service", "RuntimeDirectoryPreserve"): "yes",
    ("Service", "ProtectSystem"): "strict",
    ("Service", "ProtectHome"): "yes",
    ("Service", "PrivateTmp"): "yes",
    ("Service", "NoNewPrivileges"): "yes",
    ("Install", "WantedBy"): "multi-user.target",
}

UPDATE_CHECK = "airymax-update-check.service"

CHECK_PAIRS: dict[tuple[str, str], str] = {
    ("Unit", "After"): "network-online.target",
    ("Unit", "Wants"): "network-online.target",
    ("Service", "Type"): "oneshot",
    ("Service", "ExecStart"): "/usr/bin/airy-update check",
    # 10 表示「有新版」：按失败上报会让每次发现更新都把单元记成 failed，
    # 运维看到的是红点而不是「今天有更新」。2 表示「没连上、本次未判定」：
    # 离线机器每次开机都会跑本单元，把它算失败会让 failed 列表常年挂着一条
    # 与产品无关的红项，真实故障反被盖住。
    ("Service", "SuccessExitStatus"): "10 2",
    ("Service", "StateDirectory"): "airy-update",
    ("Service", "TimeoutStartSec"): "300",
    ("Service", "ProtectSystem"): "strict",
    ("Service", "ProtectHome"): "yes",
    ("Service", "PrivateTmp"): "yes",
    ("Service", "NoNewPrivileges"): "yes",
}

LLM = "airy-llm.service"

AGENT_SLICE = "airymax-agent.slice"

# 推理单元的峰值内存取决于权重尺寸，绝对值上限会在换机型时把能跑的模型
# 杀掉；比例上限则与机型无关，且正是 285H 需要的约束——现场三个实例把
# RSS 推到 29.5G / 30G 后，整机进入 direct reclaim，桌面与 SSH 一起失去
# 响应。只声明 v1 hybrid 上真正会落地的属性：MemoryHigh 是 v2 专有属性，
# 在 v1 上根本不写 memory.soft_limit_in_bytes，声明了只会制造「看起来有
# 软限」的假象；MemorySwapMax 则落成 memory.memsw.limit_in_bytes，实测
# 该文件可写、组内 swap 记账非零，因此换页上限必须与内存上限一起声明，
# 否则超额页只是被换出，整机仍在换出与缺页之间打转。上限只在 slice 上
# 声明一处，推理单元自身不再重复 MemoryMax。
AGENT_SLICE_PAIRS: dict[tuple[str, str], str] = {
    ("Unit", "Description"): "AirymaxOS model and agent workload slice",
    ("Unit", "Before"): "slices.target",
    ("Slice", "CPUWeight"): "100",
    ("Slice", "IOWeight"): "100",
    ("Slice", "MemoryMax"): "90%",
    ("Slice", "MemorySwapMax"): "10%",
    ("Slice", "TasksMax"): "80%",
}

# 推理单元不是 agentrt 的 daemon：它只消费首启准备好的清单与资产，不参与
# 依赖矩阵，也不申请任何 capability。内存上限由 airymax-agent.slice 统一
# 声明，单元自身只声明归属；PrivateDevices 不能开：GPU 后端依赖 /dev 下的
# 设备节点。
LLM_PAIRS: dict[tuple[str, str], str] = {
    ("Unit", "After"): "agentrt-firstboot.service",
    ("Unit", "Requires"): "agentrt-firstboot.service",
    ("Unit", "PartOf"): "agentrt.target",
    # 清单由首启准备；缺清单时单元必须条件跳过而不是反复失败重启。
    ("Unit", "ConditionPathExists"): "/var/lib/airy-llm/manifest.json",
    ("Unit", "OnFailure"): "airy-incident@%n.service",
    ("Unit", "StartLimitBurst"): "3",
    ("Unit", "StartLimitIntervalSec"): "300",
    ("Service", "Type"): "simple",
    ("Service", "ExecStart"): "/usr/bin/airy-model serve",
    ("Service", "Restart"): "on-failure",
    ("Service", "RestartSec"): "5s",
    # 准入拒绝(75)与资产缺失(78)是确定结论，重试只会在更紧的内存里再撞一次。
    ("Service", "RestartPreventExitStatus"): "75 78",
    ("Service", "TimeoutStopSec"): "30",
    ("Service", "KillMode"): "mixed",
    ("Service", "Slice"): AGENT_SLICE,
    ("Service", "User"): "airy",
    ("Service", "Group"): "airy",
    ("Service", "SupplementaryGroups"): "airy-cogn",
    ("Service", "TasksMax"): "512",
    ("Service", "LimitNOFILE"): "65536",
    ("Service", "StateDirectory"): "airy-llm",
    ("Service", "StateDirectoryMode"): "0750",
    ("Service", "ReadWritePaths"): "/var/lib/airy-llm",
    ("Service", "ProtectSystem"): "strict",
    ("Service", "ProtectHome"): "yes",
    ("Service", "ProtectKernelTunables"): "yes",
    ("Service", "ProtectKernelModules"): "yes",
    ("Service", "ProtectControlGroups"): "yes",
    ("Service", "PrivateTmp"): "yes",
    ("Service", "RestrictAddressFamilies"): "AF_UNIX AF_INET AF_INET6",
    ("Service", "RestrictSUIDSGID"): "yes",
    ("Service", "RemoveIPC"): "yes",
    ("Service", "NoNewPrivileges"): "yes",
    ("Service", "CapabilityBoundingSet"): "",
    ("Service", "AmbientCapabilities"): "",
    ("Service", "IOSchedulingClass"): "best-effort",
    ("Service", "IOSchedulingPriority"): "4",
    ("Service", "SyslogIdentifier"): "airy-llm",
    ("Service", "StandardOutput"): "journal",
    ("Service", "StandardError"): "journal",
    ("Service", "OOMPolicy"): "stop",
    ("Install", "WantedBy"): "agentrt.target",
}

SESSION_DROPIN = "user.slice.d/50-airymax-memory.conf"

# 会话池是所有用户会话 scope 的共同父节点：AirymaxRT 直接 exec 出来的推理
# 进程不在任何单元里，只有这一层能兜住。85% 留一成半给内核、system.slice
# 与回写；硬上限只声明 MemoryMax，理由同 airymax-agent.slice（v1 hybrid 不
# 落地 MemoryHigh）。换页上限按同样的比例补齐到物理内存一整个：只限内存时
# 超额页会转进 swap（285H 实测组内 13.8G），整机仍在换页路径上失速。
# 上层就是不设上限的 root cgroup，改这里等于改整机。
SESSION_PAIRS: dict[tuple[str, str], str] = {
    ("Slice", "MemoryMax"): "85%",
    ("Slice", "MemorySwapMax"): "15%",
}

FSCK_DROPIN = "systemd-fsck.d/50-console-ascii.conf"

# 发行版 fsck 单元只写 stdout、不显式声明 StandardOutput，结论默认只进
# journal。e2fsck 卡住时屏幕上没有任何线索，因此 drop-in 先把结论转发到
# 本地屏；转发之后措辞又受装机 locale 支配（zh_CN.UTF-8 → 中文），而 VT
# 字体没有 CJK 字形，整行只剩方块（285H 冷启动实测）。两个键缺一不可：
# 只补 locale 而不转发，屏幕上依旧什么都没有。
FSCK_PAIRS: dict[tuple[str, str], str] = {
    ("Service", "StandardOutput"): "journal+console",
    ("Service", "Environment"): "LANG=C LC_ALL=C",
}

DBUS_RESET_DROPIN = "dbus.socket.d/10-airymax-listen-reset.conf"
DBUS_RUN_DROPIN = "dbus.socket.d/20-airymax-listen-run.conf"

# 发行版 dbus.socket 的 ListenStream 写在 legacy 目录 /var/run 下，systemd
# 改写路径时打印一条含 U+2192 的告警；冷启动阶段它经 /dev/kmsg 直落本地屏，
# 而 VT 字体没有该字形，屏幕上只剩一个方块（285H 实测）。列表型指令的
# drop-in 只能先空赋值重置、再赋值，两份文件按文件名顺序应用。
DBUS_RESET_PAIRS: dict[tuple[str, str], str] = {
    ("Socket", "ListenStream"): "",
}

DBUS_RUN_PAIRS: dict[tuple[str, str], str] = {
    ("Socket", "ListenStream"): "/run/dbus/system_bus_socket",
}

DROPINS: dict[str, dict[tuple[str, str], str]] = {
    SESSION_DROPIN: SESSION_PAIRS,
    FSCK_DROPIN: FSCK_PAIRS,
    DBUS_RESET_DROPIN: DBUS_RESET_PAIRS,
    DBUS_RUN_DROPIN: DBUS_RUN_PAIRS,
}

# journal+console 把 journal 里的人类措辞原样搬到 VT，而 VT 字体只有拉丁
# 字形，中文与箭头符号落到屏幕上就是空白或方块（285H 冷启动实测）。这类
# 回归只能靠豁免表收口：想转发到本地屏的单元必须在这里登记并写明理由。
CONSOLE_EXEMPT: frozenset[str] = frozenset({
    # e2fsck 卡住时屏幕上没有任何线索，结论必须同时落本地屏；配套
    # LANG=C 保证措辞仍是 ASCII。
    FSCK_DROPIN,
})

CONSOLE_SINK_LINES: frozenset[str] = frozenset(
    f"{key}=journal+console" for key in ("StandardOutput", "StandardError")
)

TIMERS: dict[str, dict[tuple[str, str], str]] = {
    "airymax-update-check.timer": {
        ("Unit", "Description"): "Daily AirymaxOS Update Check",
        ("Timer", "OnCalendar"): "daily",
        ("Timer", "RandomizedDelaySec"): "6h",
        ("Timer", "Persistent"): "true",
        ("Install", "WantedBy"): "timers.target",
    },
    "airy-disk-health.timer": {
        ("Unit", "Description"): (
            "Daily AirymaxOS Data Partition Health Check"
        ),
        ("Timer", "OnCalendar"): "daily",
        ("Timer", "Persistent"): "true",
        ("Install", "WantedBy"): "timers.target",
    },
    "airy-power.timer": {
        ("Unit", "Description"): (
            "Periodic AirymaxOS Power and Thermal Re-evaluation"
        ),
        ("Timer", "OnBootSec"): "120s",
        ("Timer", "OnUnitInactiveSec"): "60s",
        ("Timer", "AccuracySec"): "15s",
        ("Timer", "Unit"): POWER,
        ("Install", "WantedBy"): "timers.target",
    },
    "airy-rt-coexist.timer": {
        ("Unit", "Description"): (
            "Periodic AirymaxOS Desktop and Real-Time Coexistence Check"
        ),
        ("Timer", "OnBootSec"): "90s",
        ("Timer", "OnUnitInactiveSec"): "60s",
        ("Timer", "AccuracySec"): "15s",
        ("Timer", "Unit"): COEXIST,
        ("Install", "WantedBy"): "timers.target",
    },
    "airy-rt-memcap.timer": {
        ("Unit", "Description"): (
            "Periodic AirymaxOS memory and swap ceiling re-application"
        ),
        ("Timer", "OnBootSec"): "120s",
        ("Timer", "OnUnitInactiveSec"): "60s",
        ("Timer", "AccuracySec"): "15s",
        ("Timer", "Unit"): MEMCAP,
        ("Install", "WantedBy"): "timers.target",
    },
    # 挂 sysinit.target 而不是 timers.target：后者由 basic.target 拉起，
    # 而 basic 排在 sysinit 之后。sysinit 被卡住时，挂 timers.target 的
    # 定时器永不启动，看门狗与被观察者一同沉没。Before=sysinit.target 让
    # 计时器注册随 sysinit 的启动事务立即发生；DefaultDependencies=no 去掉
    # 定时器缺省带入的 Requires/After=sysinit.target，否则与 Before 成环。
    "airy-boot-watch.timer": {
        ("Unit", "Description"): (
            "Report a boot that stops making progress"
        ),
        ("Unit", "DefaultDependencies"): "no",
        ("Unit", "Before"): "sysinit.target",
        ("Timer", "OnBootSec"): "60s",
        ("Timer", "AccuracySec"): "5s",
        ("Timer", "Unit"): BOOT_WATCH,
        ("Install", "WantedBy"): "sysinit.target",
    },
}

UNITS = frozenset(SERVICES) | frozenset(TARGETS) | frozenset(TEMPLATES) | {
    AGENT_SLICE,
    BOOT_MOUNT,
    BOOT_WATCH,
    COEXIST,
    DISK_HEALTH,
    DISK_MOUNT,
    FIRSTBOOT,
    HEALTH,
    LLM,
    MEMCAP,
    NET_DOCTOR,
    POWER,
    RT_BANDWIDTH,
    UPDATE_CHECK,
} | frozenset(TIMERS)

_SECTIONS = frozenset({"Unit", "Service", "Slice", "Timer", "Install"})
_META = frozenset({
    ("Unit", "Description"),
    ("Unit", "Documentation"),
})
_DEPS = ("After", "Requires", "Wants", "Conflicts", "Before")


def _tokens(value: str | None) -> list[str]:
    return (value or "").split()


def _env(value: str | None) -> dict[str, str]:
    """把 [Service] Environment 的 `A=1 B=2` 拆成映射。"""
    pairs: dict[str, str] = {}
    for item in _tokens(value):
        key, sep, val = item.partition("=")
        if sep:
            pairs[key] = val
    return pairs


def _seconds(value: str | None, fallback: int) -> int | None:
    """秒数取整；缺省用 fallback，非整数返回 None。"""
    if value is None:
        return fallback
    try:
        return int(value)
    except ValueError:
        return None


def _get(cfg: ConfigParser, section: str, key: str) -> str | None:
    if not cfg.has_section(section):
        return None
    return cfg.get(section, key, fallback=None)


def _deps(cfg: ConfigParser) -> dict[str, list[str]]:
    return {key: _tokens(_get(cfg, "Unit", key)) for key in _DEPS}


def _pairs(spec: Service) -> dict[tuple[str, str], str | None]:
    exec_start = spec.exec_start or (
        f"/usr/sbin/{spec.daemon}_d"
        f" --config=/etc/agentrt/daemons/{spec.daemon}_d.conf"
    )
    pairs: dict[tuple[str, str], str | None] = {
        ("Unit", "After"): f"{spec.after} {FIRSTBOOT}".strip(),
        ("Unit", "Requires"): f"{spec.requires} {FIRSTBOOT}".strip(),
        ("Unit", "Wants"): spec.wants or None,
        ("Unit", "PartOf"): "agentrt.target",
        ("Unit", "OnFailure"): "airy-incident@%n.service",
        ("Service", "Type"): "notify",
        ("Service", "NotifyAccess"): "main",
        ("Service", "ExecStart"): exec_start,
        ("Service", "ExecReload"): "/bin/kill -HUP $MAINPID",
        ("Service", "Restart"): spec.restart,
        ("Service", "RestartSec"): spec.restart_sec,
        # StartLimit* belongs to [Unit] since systemd v229; declared in
        # [Service] they are ignored (StartLimitIntervalSec) and the
        # restart window silently falls back to the 10s default.
        ("Unit", "StartLimitBurst"): spec.burst,
        ("Unit", "StartLimitIntervalSec"): "300",
        ("Service", "WatchdogSec"): "30s",
        ("Service", "TimeoutStartSec"): "30",
        ("Service", "TimeoutStopSec"): "15",
        ("Service", "KillMode"): "mixed",
        ("Service", "User"): "airy",
        ("Service", "Group"): "airy",
        ("Service", "SupplementaryGroups"): spec.groups,
        ("Service", "MemoryMax"): spec.mem_max,
        ("Service", "MemoryHigh"): spec.mem_high,
        ("Service", "CPUQuota"): spec.cpu,
        ("Service", "TasksMax"): spec.tasks,
        ("Service", "LimitNOFILE"): spec.nofile,
        ("Service", "ProtectSystem"): "strict",
        ("Service", "ProtectHome"): "yes",
        ("Service", "PrivateTmp"): "yes",
        ("Service", "NoNewPrivileges"): "yes",
        ("Service", "CapabilityBoundingSet"): spec.caps,
        ("Service", "AmbientCapabilities"): spec.ambient,
        ("Service", "ReadWritePaths"): spec.rw or None,
        ("Service", "IOSchedulingClass"): spec.io_class,
        ("Service", "IOSchedulingPriority"): spec.io_prio,
        ("Service", "SyslogIdentifier"): f"{spec.daemon}_d",
        ("Service", "LogLevelMax"): "info",
        ("Service", "StandardOutput"): "journal",
        ("Service", "StandardError"): "journal",
        ("Service", "OOMPolicy"): "stop",
        ("Install", "WantedBy"): "agentrt.target",
    }
    for section, key in spec.omit:
        pairs.pop((section, key), None)
    for section, key, value in spec.extra:
        pairs[(section, key)] = value
    return pairs


def _diff(
    name: str,
    cfg: ConfigParser,
    pairs: dict[tuple[str, str], str | None],
) -> list[str]:
    return [
        f"{name}: [{section}] {key} is"
        f" {_get(cfg, section, key)!r}, expected {value!r}"
        for (section, key), value in pairs.items()
        if _get(cfg, section, key) != value
    ]


def _extra_bad(
    name: str,
    cfg: ConfigParser,
    pairs: dict[tuple[str, str], str | None],
    allow: frozenset[tuple[str, str]] = frozenset(),
) -> list[str]:
    allowed = set(pairs) | _META | allow
    return [
        f"{name}: unexpected directive [{section}] {key}"
        for section in cfg.sections()
        for key in cfg.options(section)
        if (section, key) not in allowed
    ]


def _sections_bad(name: str, cfg: ConfigParser) -> list[str]:
    return [
        f"{name}: unexpected section [{section}]"
        for section in cfg.sections()
        if section not in _SECTIONS
    ]


def _meta_bad(name: str, cfg: ConfigParser) -> list[str]:
    if _get(cfg, "Unit", "Description"):
        return []
    return [f"{name}: missing [Unit] Description"]


def _order_bad(name: str, deps: dict[str, list[str]]) -> list[str]:
    missing = sorted(set(deps["Requires"]) - set(deps["After"]))
    if not missing:
        return []
    return [f"{name}: Requires without After: {' '.join(missing)}"]


def _ref_bad(
    name: str,
    deps: dict[str, list[str]],
    known: frozenset[str] | set[str],
) -> list[str]:
    return [
        f"{name}: {key} references unknown unit {unit}"
        for key, units in deps.items()
        for unit in units
        if unit not in known and unit != "%i"
    ]


def _graph(parsed: dict[str, ConfigParser]) -> dict[str, set[str]]:
    names = set(parsed)
    edges: dict[str, set[str]] = {name: set() for name in names}
    for name, cfg in parsed.items():
        deps = _deps(cfg)
        edges[name] |= {unit for unit in deps["After"] if unit in names}
        for unit in deps["Before"]:
            if unit in names:
                edges[unit].add(name)
    return edges


def _cycle_bad(edges: dict[str, set[str]]) -> list[str]:
    indeg = dict.fromkeys(edges, 0)
    for targets in edges.values():
        for target in targets:
            indeg[target] += 1
    ready = [node for node, deg in indeg.items() if deg == 0]
    seen = 0
    while ready:
        node = ready.pop()
        seen += 1
        for target in edges[node]:
            indeg[target] -= 1
            if indeg[target] == 0:
                ready.append(target)
    if seen == len(edges):
        return []
    return ["unit dependency cycle detected in After graph"]


def _set_bad(
    name: str,
    cfg: ConfigParser,
    key: str,
    expect: tuple[str, ...],
) -> list[str]:
    got = _tokens(_get(cfg, "Unit", key))
    if set(got) == set(expect) and len(got) == len(expect):
        return []
    return [
        f"{name}: {key} set {sorted(got)} != {sorted(set(expect))}"
    ]


def _oneshot_bad(name: str, cfg: ConfigParser) -> list[str]:
    """oneshot 单元必须显式给出有限的启动超时。

    Type=oneshot 的超时缺省为 infinity：ExecStart 一旦阻塞，启动事务就
    无界等待，屏幕停在最后一行，现场只能靠猜。285H 的 plymouth 卡死正是
    这一类。infinity、0 与非数字与「没写」等效或更糟，一律判负。
    """
    if _get(cfg, "Service", "Type") != "oneshot":
        return []
    value = _get(cfg, "Service", "TimeoutStartSec")
    number = _seconds(value, 0)
    if number is not None and number > 0:
        return []
    return [
        f"{name}: Type=oneshot needs a finite TimeoutStartSec"
        f" (got {value!r})"
    ]


def _service_bad(name: str, cfg: ConfigParser) -> list[str]:
    pairs = _pairs(SERVICES[name])
    return (
        _diff(name, cfg, pairs)
        + _extra_bad(name, cfg, pairs)
        + _order_bad(name, _deps(cfg))
    )


def _target_bad(name: str, cfg: ConfigParser) -> list[str]:
    spec = TARGETS[name]
    pairs: dict[tuple[str, str], str | None] = {
        ("Unit", "After"): spec.after or None,
        ("Unit", "Conflicts"): spec.conflicts or None,
        ("Unit", "AllowIsolate"): "yes",
        ("Install", "WantedBy"): "multi-user.target",
    }
    return (
        _diff(name, cfg, pairs)
        + _extra_bad(name, cfg, pairs, frozenset({("Unit", "Requires")}))
        + _set_bad(name, cfg, "Requires", spec.requires)
    )


def _incident_bad(name: str, cfg: ConfigParser) -> list[str]:
    pairs = {(section, key): value for section, key, value in TEMPLATES[name]}
    return _diff(name, cfg, pairs) + _extra_bad(name, cfg, pairs)


def _pairs_bad(
    name: str,
    cfg: ConfigParser,
    pairs: dict[tuple[str, str], str | None],
) -> list[str]:
    return (
        _diff(name, cfg, pairs)
        + _extra_bad(name, cfg, pairs)
        + _order_bad(name, _deps(cfg))
    )


STAGE_RUN = "stage_run.sh"
STAGE_BEAT = 30
STAGE_LIMIT = 60
UI_SH = ROOT / "scripts" / "ui.sh"
UI_ROW = re.compile(r"^\s*'(\d+)\|([^|']+)\|(\d+)\|([^|']+)'\s*$", re.M)


def ui_path(units: Path) -> Path:
    """与单元目录同仓的 ui.sh 路径。

    Args:
        units: 单元目录。

    Returns:
        ui.sh 的路径。
    """
    return units.parent / "scripts" / "ui.sh"


def ui_rows(path: Path = UI_SH) -> dict[str, tuple[int, int, str]]:
    """读取 ui.sh 的阶段契约表。

    Args:
        path: ui.sh 路径。

    Returns:
        阶段名 → (序号, 显示宽度, ASCII 代号)；表不可读时为空字典。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    return {
        match.group(2): (
            int(match.group(1)),
            int(match.group(3)),
            match.group(4),
        )
        for match in UI_ROW.finditer(text)
    }


def _columns(name: str) -> int:
    """阶段名的显示宽度：东亚宽字符记两列，其余记一列。"""
    return sum(
        2 if unicodedata.east_asian_width(char) in "WF" else 1
        for char in name
    )


def _ui_table_bad(path: Path = UI_SH) -> list[str]:
    """阶段契约表必须连续编号、名称不含空白、宽度与字符一致、代号纯 ASCII。

    单元以 ExecStart 传阶段名，systemd 按空白切分 ExecStart：名字里带空白
    会被拆成多个参数，运行期查不到序号而以非零退出，现场只剩一行 FAILED
    （run 41802417971 的装机首启）。宽度写错则九行打印不再对齐，「卡在哪
    一步」要靠肉眼从错位的列里猜。代号是 console 侧唯一措辞，VT 字体没有
    CJK 字形，非 ASCII 代号会让本已修好的开屏重新变成乱码。

    Args:
        path: ui.sh 路径。

    Returns:
        违约描述列表，空列表表示通过。
    """
    rows = ui_rows(path)
    if not rows:
        return [f"{path.name}: 阶段契约表缺失或为空"]
    errs = []
    if sorted(index for index, _, _ in rows.values()) != list(
        range(1, len(rows) + 1)
    ):
        errs.append(f"{path.name}: 阶段序号必须从 1 连续编号")
    errs.extend(
        f"{path.name}: 阶段名含空白，ExecStart 会把它拆成多个参数: {name!r}"
        for name in rows
        if any(char.isspace() for char in name)
    )
    errs.extend(
        f"{path.name}: 阶段 {name} 宽度 {width} != 实际 {_columns(name)}"
        for name, (_, width, _) in rows.items()
        if width != _columns(name)
    )
    errs.extend(
        f"{path.name}: 阶段 {name} 的 console 代号不是纯 ASCII: {code!r}"
        for name, (_, _, code) in rows.items()
        if not code.isascii() or code != code.strip()
    )
    codes = [code for _, _, code in rows.values()]
    errs.extend(
        f"{path.name}: console 代号重复: {code}"
        for code in sorted({code for code in codes if codes.count(code) > 1})
    )
    return errs


def _stage_arg_bad(
    name: str, cfg: ConfigParser, rows: dict[str, tuple[int, int, str]]
) -> list[str]:
    """ExecStart 的阶段名必须是单个参数且登记在契约表内。

    Args:
        name: 单元文件名。
        cfg: 已解析的单元配置。
        rows: 阶段契约表。

    Returns:
        违约描述列表，空列表表示通过。
    """
    tokens = (_get(cfg, "Service", "ExecStart") or "").split()
    index = next(
        (pos for pos, tok in enumerate(tokens) if tok.endswith(STAGE_RUN)),
        None,
    )
    if index is None:
        return []
    if index + 1 >= len(tokens):
        return [f"{name}: ExecStart 缺阶段名"]
    stage = tokens[index + 1]
    if stage not in rows:
        return [f"{name}: 阶段名 {stage!r} 不在 ui.sh 契约表"]
    return []


def _stage_bad(name: str, cfg: ConfigParser) -> list[str]:
    """阶段单元的心跳周期与预期上限必须与 TimeoutStartSec 同源。

    stage_run.sh 在工具迟迟不返回时按 AIRY_STAGE_BEAT 补 wait 行，并把
    AIRY_STAGE_LIMIT 打给现场当预期上限；真正的截断仍由单元的
    TimeoutStartSec 执行。两者不同源时，现场先被误导再被静默杀掉：上限
    写着 60 秒，30 秒就没了下文；或者周期恰好等于超时，一行心跳都来不及
    落，屏幕上只剩一条没有下文的「...」，卡在哪一步依然无从判断。

    因此这里要求 limit 与 TimeoutStartSec 相等，且 beat 至少能落两行心跳。
    周期与上限的缺省值同时写在本文件与 stage_run.sh，两处都由测试比对。
    """
    if STAGE_RUN not in (_get(cfg, "Service", "ExecStart") or ""):
        return []
    env = _env(_get(cfg, "Service", "Environment"))
    timeout = _seconds(_get(cfg, "Service", "TimeoutStartSec"), 90)
    beat = _seconds(env.get("AIRY_STAGE_BEAT"), STAGE_BEAT)
    limit = _seconds(env.get("AIRY_STAGE_LIMIT"), STAGE_LIMIT)
    errs = [
        f"{name}: {key} must be integer seconds"
        for key, value in (
            ("TimeoutStartSec", timeout),
            ("AIRY_STAGE_BEAT", beat),
            ("AIRY_STAGE_LIMIT", limit),
        )
        if value is None
    ]
    if errs:
        return errs
    if limit != timeout:
        errs.append(
            f"{name}: AIRY_STAGE_LIMIT {limit}s != TimeoutStartSec {timeout}s"
        )
    if beat * 2 > timeout:
        errs.append(
            f"{name}: AIRY_STAGE_BEAT {beat}s leaves no heartbeat before"
            f" TimeoutStartSec {timeout}s"
        )
    errs.extend(_ui_dir_bad(name, cfg))
    return errs


def _ui_dir_bad(name: str, cfg: ConfigParser) -> list[str]:
    """阶段单元的沙箱必须放行去重标记目录。

    ui.sh 的闸门把「本阶段本次开机是否已出结论」记在全局共享的
    /run/airymax-ui 下，靠它让 timer 重跑的同阶段只出一行。ProtectSystem=
    strict 会把 /run 一并挂成只读，此时目录若已由别的单元建好，mkdir -p
    返回 0、写标记才以 EROFS 失败，闸门被静默跳过，阶段行每 60 秒重印一条
    （run 42007477579 的 `/run/airymax-ui/5: Read-only file system`）。
    声明 RuntimeDirectory=airymax-ui 让 systemd 保证该路径可写，是唯一
    不依赖调用顺序的放行方式。
    """
    protect = _get(cfg, "Service", "ProtectSystem")
    if protect not in ("strict", "full"):
        return []
    dirs = (_get(cfg, "Service", "RuntimeDirectory") or "").split()
    if "airymax-ui" in dirs:
        return []
    return [
        f"{name}: ProtectSystem={protect} hides /run/airymax-ui;"
        " declare RuntimeDirectory=airymax-ui for the stage gate"
    ]


def _timer_bad(name: str, cfg: ConfigParser) -> list[str]:
    return _pairs_bad(name, cfg, dict(TIMERS[name]))


# 名字 → 成对判据表。值在调用时复制，判据表本身只读；新增只声明键值的
# 单元在这里加一行即可，没有登记的名字会落到模板判据，而漏登记本身由
# check_dir 的 missing/unexpected unit 两条判据兜住。
PAIR_SPECS: dict[str, dict[tuple[str, str], str | None]] = {
    FIRSTBOOT: FIRSTBOOT_PAIRS,
    HEALTH: HEALTH_PAIRS,
    BOOT_MOUNT: BOOT_MOUNT_PAIRS,
    BOOT_WATCH: BOOT_WATCH_PAIRS,
    NET_DOCTOR: NET_DOCTOR_PAIRS,
    DISK_MOUNT: DISK_MOUNT_PAIRS,
    DISK_HEALTH: DISK_HEALTH_PAIRS,
    RT_BANDWIDTH: RT_PAIRS,
    MEMCAP: MEMCAP_PAIRS,
    POWER: POWER_PAIRS,
    COEXIST: COEXIST_PAIRS,
    UPDATE_CHECK: CHECK_PAIRS,
    LLM: LLM_PAIRS,
    AGENT_SLICE: AGENT_SLICE_PAIRS,
}


def _body_bad(name: str, cfg: ConfigParser) -> list[str]:
    if name in SERVICES:
        return _service_bad(name, cfg)
    if name in TARGETS:
        return _target_bad(name, cfg)
    pairs = PAIR_SPECS.get(name)
    if pairs is not None:
        return _pairs_bad(name, cfg, dict(pairs))
    if name in TIMERS:
        return _timer_bad(name, cfg)
    return _incident_bad(name, cfg)


def _doc_bad(name: str, cfg: ConfigParser) -> list[str]:
    return (
        _sections_bad(name, cfg)
        + _meta_bad(name, cfg)
        + _ref_bad(name, _deps(cfg), UNITS | SYSTEM_UNITS)
    )


def _text_bad(name: str, raw: bytes) -> list[str]:
    errs: list[str] = []
    if b"\r" in raw:
        errs.append(f"{name}: CRLF line endings")
    if b"\t" in raw:
        errs.append(f"{name}: tab characters")
    if not raw.endswith(b"\n"):
        errs.append(f"{name}: missing final newline")
    text = raw.decode("utf-8")
    errs.extend(
        f"{name}:{line_no}: trailing whitespace"
        for line_no, line in enumerate(text.splitlines(), 1)
        if line != line.rstrip()
    )
    return errs


def _file_bad(path: Path) -> list[str]:
    errs = _text_bad(path.name, path.read_bytes())
    if path.stat().st_mode & 0o777 != 0o644:
        errs.append(f"{path.name}: mode must be 0644")
    return errs


def _parse(path: Path) -> tuple[ConfigParser | None, list[str]]:
    cfg = ConfigParser(interpolation=None, strict=True)
    cfg.optionxform = str
    try:
        cfg.read_string(path.read_text(encoding="utf-8"))
    except IniError as exc:
        return None, [f"{path.name}: {exc}"]
    return cfg, []


def _dropin_bad(path: Path, name: str) -> list[str]:
    errs = _file_bad(path)
    cfg, parse_errs = _parse(path)
    errs.extend(parse_errs)
    if cfg is None:
        return errs
    return errs + _pairs_bad(name, cfg, dict(DROPINS[name]))


def _console_bad(rel: str, path: Path) -> list[str]:
    """拒绝未经豁免就把输出转发到本地控制台的单元。

    Args:
        rel: 相对单元目录的路径，用于豁免比对与报错。
        path: 单元文件路径。

    Returns:
        违约描述列表，空列表表示通过。
    """
    if rel in CONSOLE_EXEMPT:
        return []
    text = path.read_text(encoding="utf-8")
    return [
        f"{rel}:{line_no}: console forwarding needs an exemption"
        for line_no, line in enumerate(text.splitlines(), 1)
        if line.strip() in CONSOLE_SINK_LINES
    ]


def check_file(path: Path) -> list[str]:
    """校验单个单元文件。

    Args:
        path: 单元文件路径。

    Returns:
        违约描述列表，空列表表示通过。
    """
    errs = _file_bad(path)
    cfg, parse_errs = _parse(path)
    errs.extend(parse_errs)
    if cfg is None:
        return errs
    return (
        errs
        + _doc_bad(path.name, cfg)
        + _body_bad(path.name, cfg)
        + _stage_bad(path.name, cfg)
        + _oneshot_bad(path.name, cfg)
        + _console_bad(path.name, path)
        + _stage_arg_bad(
            path.name, cfg, ui_rows(ui_path(path.parent))
        )
    )


def check_script(path: Path = SCRIPT) -> list[str]:
    """校验随单元交付的运行时脚本。

    Args:
        path: 脚本路径，缺省为健康自检脚本。

    Returns:
        违约描述列表，空列表表示通过。
    """
    if not path.is_file():
        return [f"{path.name}: missing script"]
    errs = _text_bad(path.name, path.read_bytes())
    mode = path.stat().st_mode & 0o777
    if mode != 0o755:
        errs.append(f"{path.name}: mode must be 0755, got {mode:04o}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != SHEBANG:
        errs.append(f"{path.name}: shebang must be {SHEBANG!r}")
    if "set -euo pipefail" not in lines:
        errs.append(f"{path.name}: missing 'set -euo pipefail'")
    return errs


def _tree_bad(path: Path, names: set[str]) -> list[str]:
    """名字集合与磁盘实物的差异，以及 drop-in 树的内容。

    Args:
        path: 单元目录路径。
        names: 目录下顶层文件名集合。

    Returns:
        违约描述列表，逐条列出缺件与多件。
    """
    errs = [f"missing unit {name}" for name in sorted(UNITS - names)]
    errs.extend(
        f"{name}: unexpected unit file" for name in sorted(names - UNITS)
    )
    nested = {
        item.relative_to(path).as_posix()
        for item in path.rglob("*")
        if item.is_file() and item.parent != path
    }
    errs.extend(
        f"missing drop-in {name}" for name in sorted(set(DROPINS) - nested)
    )
    errs.extend(
        f"{name}: unexpected drop-in file"
        for name in sorted(nested - set(DROPINS))
    )
    for name in sorted(nested & set(DROPINS)):
        errs.extend(_dropin_bad(path / name, name))
    for rel in sorted(nested | names):
        errs.extend(_console_bad(rel, path / rel))
    return errs


def _parse_all(
    units: list[Path],
) -> tuple[dict[str, ConfigParser], list[str]]:
    """解析全部单元并检查依赖环。

    Args:
        units: 待解析的单元文件。

    Returns:
        解析成功的配置与违约描述列表；解析失败的单元不进图。
    """
    parsed: dict[str, ConfigParser] = {}
    errs: list[str] = []
    for item in units:
        cfg, parse_errs = _parse(item)
        errs.extend(parse_errs)
        if cfg is not None:
            parsed[item.name] = cfg
    errs.extend(_cycle_bad(_graph(parsed)))
    return parsed, errs


def _unit_bad(
    units: list[Path],
    parsed: dict[str, ConfigParser],
    rows: dict[str, tuple[int, int, str]],
) -> list[str]:
    """已解析单元的内容判据。

    Args:
        units: 待校验的单元文件。
        parsed: 单元名到解析结果的映射。
        rows: ui.sh 的阶段契约表。

    Returns:
        违约描述列表；解析失败的单元只报解析错，不再叠内容判据。
    """
    errs: list[str] = []
    for item in units:
        errs.extend(_file_bad(item))
        cfg = parsed.get(item.name)
        if cfg is None:
            continue
        errs.extend(_doc_bad(item.name, cfg))
        errs.extend(_body_bad(item.name, cfg))
        errs.extend(_stage_bad(item.name, cfg))
        errs.extend(_oneshot_bad(item.name, cfg))
        errs.extend(_stage_arg_bad(item.name, cfg, rows))
    return errs


def check_dir(path: Path) -> list[str]:
    """校验单元目录的全部静态契约。

    Args:
        path: 单元目录路径。

    Returns:
        违约描述列表，空列表表示全部通过。
    """
    files = sorted(item for item in path.iterdir() if item.is_file())
    errs = _tree_bad(path, {item.name for item in files})
    units = [item for item in files if item.name in UNITS]
    parsed, parse_errs = _parse_all(units)
    errs.extend(parse_errs)
    script = ui_path(path)
    rows = ui_rows(script)
    errs.extend(_ui_table_bad(script))
    errs.extend(_unit_bad(units, parsed, rows))
    errs.extend(check_script())
    for extra in (
        FIRSTBOOT_SCRIPT,
        BOOT_MOUNT_SCRIPT,
        BOOT_WATCH_SCRIPT,
        NET_DOCTOR_SCRIPT,
    ):
        errs.extend(check_script(extra))
    return errs


def main(argv: list[str] | None = None) -> int:
    """运行命令行入口。

    Args:
        argv: 参数列表，缺省取 sys.argv[1:]。

    Returns:
        0 表示全部通过，1 表示存在违约。
    """
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else UNIT_DIR
    errs = check_dir(root)
    for err in errs:
        LOG.error("%s", err)
    if errs:
        LOG.error("%d unit contract violation(s)", len(errs))
        return 1
    LOG.info("%d systemd unit(s) verified", len(UNITS))
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    sys.exit(main())
