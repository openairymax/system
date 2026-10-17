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
import sys
from configparser import ConfigParser
from configparser import Error as IniError
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "FIRSTBOOT",
    "SERVICES",
    "TARGETS",
    "TEMPLATES",
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
    "local-fs.target",
    "multi-user.target",
    "network-online.target",
    "sysinit.target",
    "systemd-journald.service",
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
            ("Service", "TimeoutStartSec"),
            ("Service", "TimeoutStopSec"),
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

TEMPLATES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "airy-incident@.service": (
        ("Unit", "Description", "Handle failure of %i"),
        ("Unit", "After", "%i"),
        ("Service", "Type", "oneshot"),
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

UNITS = frozenset(SERVICES) | frozenset(TARGETS) | frozenset(TEMPLATES) | {
    FIRSTBOOT,
    HEALTH,
}

_SECTIONS = frozenset({"Unit", "Service", "Install"})
_META = frozenset({
    ("Unit", "Description"),
    ("Unit", "Documentation"),
})
_DEPS = ("After", "Requires", "Wants", "Conflicts", "Before")


def _tokens(value: str | None) -> list[str]:
    return (value or "").split()


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
        ("Service", "StartLimitBurst"): spec.burst,
        ("Service", "StartLimitIntervalSec"): "300",
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


def _oneshot_bad(
    name: str,
    cfg: ConfigParser,
    pairs: dict[tuple[str, str], str | None],
) -> list[str]:
    return (
        _diff(name, cfg, pairs)
        + _extra_bad(name, cfg, pairs)
        + _order_bad(name, _deps(cfg))
    )


def _health_bad(name: str, cfg: ConfigParser) -> list[str]:
    return _oneshot_bad(name, cfg, dict(HEALTH_PAIRS))


def _firstboot_bad(name: str, cfg: ConfigParser) -> list[str]:
    return _oneshot_bad(name, cfg, dict(FIRSTBOOT_PAIRS))


def _body_bad(name: str, cfg: ConfigParser) -> list[str]:
    if name in SERVICES:
        return _service_bad(name, cfg)
    if name in TARGETS:
        return _target_bad(name, cfg)
    if name == FIRSTBOOT:
        return _firstboot_bad(name, cfg)
    if name == HEALTH:
        return _health_bad(name, cfg)
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
    return errs + _doc_bad(path.name, cfg) + _body_bad(path.name, cfg)


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


def check_dir(path: Path) -> list[str]:
    """校验单元目录的全部静态契约。

    Args:
        path: 单元目录路径。

    Returns:
        违约描述列表，空列表表示全部通过。
    """
    files = sorted(item for item in path.iterdir() if item.is_file())
    names = {item.name for item in files}
    errs = [f"missing unit {name}" for name in sorted(UNITS - names)]
    errs.extend(
        f"{name}: unexpected unit file"
        for name in sorted(names - UNITS)
    )
    units = [item for item in files if item.name in UNITS]
    parsed: dict[str, ConfigParser] = {}
    for item in units:
        cfg, parse_errs = _parse(item)
        errs.extend(parse_errs)
        if cfg is not None:
            parsed[item.name] = cfg
    errs.extend(_cycle_bad(_graph(parsed)))
    for item in units:
        errs.extend(_file_bad(item))
        cfg = parsed.get(item.name)
        if cfg is not None:
            errs.extend(_doc_bad(item.name, cfg))
            errs.extend(_body_bad(item.name, cfg))
    errs.extend(check_script())
    errs.extend(check_script(FIRSTBOOT_SCRIPT))
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
