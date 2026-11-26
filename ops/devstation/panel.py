# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""八面板组装：一次采集生成一个快照。

组装层只决定顺序与缓冲上限，不添加任何虚构数据。任一数据源缺失时，
对应分节保持 unavailable 并带上原因，其余分节照常出数，避免一个坏源
让整帧变空。
"""

import os
from pathlib import Path

from . import facts
from .model import Snapshot, now_ns


def node_name() -> str:
    """本机节点名；取不到时退回 localhost。"""
    try:
        return os.uname().nodename or "localhost"
    except OSError:
        return "localhost"


def build_snap(root, level, cfg) -> Snapshot:
    """按固定顺序采集八面板，返回一份完整快照。"""
    base = Path(str(root or "/"))
    log_cap = int(cfg["devstation.log_buffer_lines"])
    alert_cap = int(cfg["devstation.alert_buffer_size"])
    sections = {
        "monitor": facts.daemon_rows(base),
        "logs": facts.log_rows(base, log_cap),
        "agents": facts.agent_rows(base),
        "alerts": facts.alert_rows(base, alert_cap),
        "incidents": facts.incident_rows(base),
        "memory": facts.memory_rows(base),
        "backup": facts.backup_rows(base),
        "config": facts.config_rows(base),
    }
    return Snapshot(stamp=now_ns(), node=node_name(), level=level,
                    sections=sections)


def build_one(root, name, level, cfg) -> Snapshot:
    """只保留指定面板的快照，供单面板命令使用。"""
    full = build_snap(root, level, cfg)
    sec = full.section(name)
    return Snapshot(stamp=full.stamp, node=full.node, level=level,
                    sections={name: sec})
