# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""工作站测试的临时 fixture 构造。

一次写入八类数据源的合法样本，取值固定以便断言；构造过程使用各运维包
自己的落盘接口，避免测试与实现各自维护一套文件格式。
"""

import json
import sys
from pathlib import Path

SYS = Path(__file__).resolve().parents[2]
for _path in (SYS / "ops", SYS / "services", SYS / "services" / "daemons"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from agent import config as agent_config  # noqa: E402
from agent.store import Store as AgentStore  # noqa: E402
from backup.model import BackupRecord, Kind  # noqa: E402
from incident.model import open_incident  # noqa: E402
from incident.store import Store as IncidentStore  # noqa: E402
from memory.model import Item  # noqa: E402
from memory.store import Store as MemoryStore  # noqa: E402

DAY = 1_800_000_000_000_000_000


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _monitor(root: Path) -> None:
    folder = root / "sys/airy/monitor/daemons/airymaxmon"
    folder.mkdir(parents=True)
    for name, value in (("cpu_pct", "12.5"), ("rss_bytes", "4096"),
                        ("health_score", "98"), ("restart_count", "1"),
                        ("heartbeat_ms", "1500")):
        _write(folder / name, value + "\n")


def _logs(root: Path) -> None:
    folder = root / "var/log/airy"
    _write(folder / "app.log",
           "2026-11-27T00:00:00Z INFO airymaxmon started\n"
           "2026-11-27T00:00:01Z ERROR airy-agent failed\n")
    _write(folder / "agents/1/agent.log",
           "2026-11-27T00:00:02Z WARN airy-agent busy\n")


def _agents(root: Path) -> str:
    store = AgentStore(str(root / "var/lib/airy/agents"))
    agent = store.create(agent_config.load(None),
                         {"name": "alpha", "template": "default"})
    return agent.id


def _alerts(root: Path) -> None:
    row = {"alert_id": "A-1", "state": "FIRING", "severity": "CRITICAL",
           "source": 7, "agent": 1, "code": 101, "message": "cpu high",
           "count": 3, "first_ns": DAY, "last_ns": DAY + 1, "ack_by": ""}
    _write(root / "var/lib/airy/alerts/active.json",
           json.dumps({"schema": 1, "alerts": [row]}) + "\n")


def _incidents(root: Path) -> None:
    store = IncidentStore(str(root / "var/lib/airy/incidents"))
    inc = open_incident("INC-20261127-001", "P1", "system",
                        "daemon down", DAY, "gate")
    store.save(inc)


def _memory(root: Path) -> None:
    store = MemoryStore(str(root / "var/lib/airy/memory"))
    store.save_items("L3", "1", [Item("keep", b"x" * 8)])


def _backup(root: Path) -> None:
    rec = BackupRecord(
        backup_id="FULL-20261127-001", kind=Kind.FULL,
        created_at=DAY, completed_at=DAY + 1,
        file="FULL-20261127-001.tar.zst", compression="zstd",
        size_bytes=4096, sha256="a" * 64, total_files=3,
    )
    folder = root / "var/lib/airy/backup/full"
    _write(folder / "FULL-20261127-001.manifest.json",
           json.dumps(rec.to_dict(), sort_keys=True) + "\n")


def _config(root: Path) -> None:
    _write(root / "etc/agentrt/agent.conf",
           "# 测试配置\nagent.max_instances = 8\n")


def make(root) -> dict:
    """构造全部八类来源，返回可断言的标识。"""
    base = Path(root)
    _monitor(base)
    _logs(base)
    ident = _agents(base)
    _alerts(base)
    _incidents(base)
    _memory(base)
    _backup(base)
    _config(base)
    return {"agent_id": ident}
