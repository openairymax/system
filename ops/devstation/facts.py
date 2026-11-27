# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""只读事实采集层。

所有路径都以 root 为前缀（生产默认 /，测试指向 fixture 目录），不写任何
文件、不创建目录。数据源缺失或整体不可读时返回 status=unavailable 并带
原因，绝不以空表冒充「一切正常」。聚合数据一律复用既有运维包的公开接口。
"""

import json
import re
from pathlib import Path

from alert_d.store import Store as AlertStore
from agent.health import evaluate
from agent.store import Store as AgentStore
from backup.store import Store as BackupStore
from incident.store import Store as IncidentStore
from memory.store import Store as MemoryStore
from memory.stats import agent_status

from .model import BadState, Section, now_ns

SYS_DIR = "sys/airy/monitor/daemons"
LOG_DIR = "var/log/airy"
CONF_DIR = "etc/agentrt"
AGENT_DIRS = ("var/lib/agentrt/agents", "var/lib/airy/agents")
ALERT_DIRS = ("var/lib/agentrt/alerts", "var/lib/airy/alerts")
INCIDENT_DIRS = ("var/lib/agentrt/incidents", "var/lib/airy/incidents",
                 "var/lib/airy/incident")
MEMORY_DIRS = ("var/lib/airy/memory", "var/lib/agentrt/memory")
BACKUP_DIRS = ("var/lib/airy/backup", "var/lib/agentrt/backup")

LEVELS = ("TRACE", "DEBUG", "INFO", "WARN", "WARNING", "ERROR",
          "CRITICAL", "FATAL")
LINE = re.compile(r"^(\S+)\s+([A-Za-z]+)\s+(\S+)\s+(.*)$")
TAIL_CAP = 65536
BACKUP_KEEP = 14


def _pick(base: Path, names) -> Path | None:
    """按候选顺序返回第一个存在的目录，全部缺失返回 None。"""
    for name in names:
        got = base / name
        if got.is_dir():
            return got
    return None


def _ok(name: str, rows) -> Section:
    return Section(name=name, status="ok", rows=list(rows))


def _down(name: str, reason: str) -> Section:
    return Section(name=name, status="unavailable", reason=reason)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _num(path: Path):
    text = _read(path)
    if text is None:
        return None
    text = text.strip()
    try:
        return int(text, 10)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return None


def agent_store(base: Path):
    """返回 (AgentStore, 目录)；目录缺失抛 BadState。"""
    folder = _pick(base, AGENT_DIRS)
    if folder is None:
        raise BadState("缺少 Agent 元数据目录: " + AGENT_DIRS[0])
    return AgentStore(str(folder)), folder


def memory_store(base: Path, block_kb: int = 4):
    """返回 (MemoryStore, 目录)；目录缺失抛 BadState。"""
    folder = _pick(base, MEMORY_DIRS)
    if folder is None:
        raise BadState("缺少记忆仓库: " + MEMORY_DIRS[0])
    return MemoryStore(str(folder), block_kb), folder


def daemon_rows(base: Path) -> Section:
    """从 sysfs 读取守护进程指标。"""
    folder = base / SYS_DIR
    if not folder.is_dir():
        return _down("monitor", "缺少 " + SYS_DIR)
    rows = []
    for item in sorted(folder.iterdir(), key=lambda path: path.name):
        if not item.is_dir():
            continue
        rows.append({
            "daemon": item.name,
            "cpu_pct": _num(item / "cpu_pct"),
            "rss_bytes": _num(item / "rss_bytes"),
            "health_score": _num(item / "health_score"),
            "restarts": _num(item / "restart_count"),
            "heartbeat_ms": _num(item / "heartbeat_ms"),
        })
    return _ok("monitor", rows)


def _tail(path: Path) -> list:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > TAIL_CAP:
                handle.seek(size - TAIL_CAP)
            data = handle.read()
    except OSError:
        return []
    lines = data.splitlines()
    if size > TAIL_CAP and lines:
        lines = lines[1:]
    return [line.decode("utf-8", "replace") for line in lines]


def _level(value) -> str:
    """级别归一：缺省为 `-`，WARNING 与 WARN 合成一个词。"""
    got = str(value or "-")
    return "WARN" if got.upper() == "WARNING" else got


def _json_obj(text: str):
    try:
        row = json.loads(text)
    except ValueError:
        return None
    if not isinstance(row, dict):
        return None
    return row


def _stamp(row) -> str:
    return str(row.get("ts") or row.get("at") or "-")


def _label(row) -> str:
    return _level(row.get("level") or row.get("severity"))


def _origin(row) -> str:
    return str(row.get("source") or row.get("daemon") or "-")


def _body(row) -> str:
    return str(row.get("message") or row.get("msg") or "")


def _json_row(text: str):
    """解析一行 JSON 日志；不是对象或不可解析时返回 None。"""
    row = _json_obj(text)
    if row is None:
        return None
    return (_stamp(row), _label(row), _origin(row), _body(row))


def _parse(text: str) -> tuple:
    text = text.strip()
    if not text:
        return None
    if text.startswith("{"):
        got = _json_row(text)
        if got is not None:
            return got
    found = LINE.match(text)
    if found:
        stamp, level, source, message = found.groups()
        return (stamp, _level(level), source, message)
    return ("-", "-", "-", text)


def _row(path: Path, offset: int, got: tuple) -> dict:
    stamp, level, source, message = got
    return {"ts": stamp, "level": level, "source": source,
            "message": message, "file": path.name, "line": offset + 1}


def _collect(paths, rows: list) -> int:
    """逐文件展开日志尾部；返回确实读不到内容的文件数。"""
    failed = 0
    for path in paths:
        text = _tail(path)
        if not text:
            failed += 1 if path.is_file() else 0
            continue
        for offset, raw in enumerate(text):
            got = _parse(raw)
            if got is not None:
                rows.append(_row(path, offset, got))
    return failed


def log_rows(base: Path, limit: int) -> Section:
    """聚合 /var/log/airy 下普通日志与按 Agent 分目录的日志尾部。"""
    folder = base / LOG_DIR
    if not folder.is_dir():
        return _down("logs", "缺少 " + LOG_DIR)
    files = sorted(folder.glob("*.log"), key=str)
    files += sorted(folder.glob("agents/*/*.log"), key=str)
    rows: list = []
    failed = _collect(files, rows)
    if failed and not rows:
        return _down("logs", "日志文件不可读: %d 个" % failed)
    if limit > 0:
        rows = rows[-limit:]
    return _ok("logs", rows)


def agent_rows(base: Path) -> Section:
    """读取 Agent 元数据并计算健康分；坏记录整体标为不可用。"""
    try:
        store, _ = agent_store(base)
        agents = store.rows(None)
    except BadState as exc:
        return _down("agents", str(exc))
    except Exception as exc:  # 存储层坏记录需要如实上报
        return _down("agents", "Agent 元数据不可读: %s" % exc)
    stamp = now_ns()
    log_root = base / LOG_DIR / "agents"
    rows = []
    for agent in agents:
        try:
            health = evaluate(agent, log_root, stamp)
        except Exception as exc:
            health = {"score": None, "grade": "UNKNOWN",
                      "error": str(exc)}
        rows.append({
            "id": agent.id,
            "name": agent.name,
            "state": agent.state.value,
            "score": health.get("score"),
            "grade": health.get("grade"),
            "pid": agent.pid,
        })
    return _ok("agents", rows)


def alert_rows(base: Path, limit: int) -> Section:
    """读取告警聚合器的活动告警。"""
    folder = _pick(base, ALERT_DIRS)
    if folder is None:
        return _down("alerts", "缺少告警目录: " + ALERT_DIRS[0])
    try:
        rows = AlertStore(str(folder)).load()
    except Exception as exc:
        return _down("alerts", "告警台账不可读: %s" % exc)
    if not isinstance(rows, list):
        return _down("alerts", "告警台账格式非法")
    if limit > 0:
        rows = rows[-limit:]
    return _ok("alerts", rows)


def incident_rows(base: Path) -> Section:
    """读取事件台账。"""
    folder = _pick(base, INCIDENT_DIRS)
    if folder is None:
        return _down("incidents", "缺少事件目录: " + INCIDENT_DIRS[0])
    try:
        rows = [item.to_json() for item in IncidentStore(str(folder)).rows()]
    except Exception as exc:
        return _down("incidents", "事件台账不可读: %s" % exc)
    return _ok("incidents", rows)


def memory_rows(base: Path) -> Section:
    """按 Agent 汇总 L3/L4 记忆用量。"""
    folder = _pick(base, MEMORY_DIRS)
    if folder is None:
        return _down("memory", "缺少记忆仓库: " + MEMORY_DIRS[0])
    try:
        store = MemoryStore(str(folder))
        names = sorted(set(store.ids("L3")) | set(store.ids("L4")))
        rows = []
        for name in names:
            report = agent_status(store, name, ["L3", "L4"])
            row = {"agent": name, "exists": report.get("exists")}
            for level in ("L3", "L4"):
                stats = report["levels"][level]
                row[level.lower() + "_items"] = stats.get("items", 0)
                row[level.lower() + "_bytes"] = stats.get("live_bytes", 0)
                row[level.lower() + "_frag"] = stats.get(
                    "fragmentation_pct", 0.0
                )
            rows.append(row)
    except Exception as exc:
        return _down("memory", "记忆仓库不可读: %s" % exc)
    return _ok("memory", rows)


def backup_rows(base: Path) -> Section:
    """读取备份仓库索引中的记录。"""
    folder = _pick(base, BACKUP_DIRS)
    if folder is None:
        return _down("backup", "缺少备份仓库: " + BACKUP_DIRS[0])
    try:
        records = BackupStore(str(folder), BACKUP_KEEP).records()
    except Exception as exc:
        return _down("backup", "备份索引不可读: %s" % exc)
    rows = []
    for item in records:
        rows.append({
            "backup_id": item.backup_id,
            "kind": item.kind.value,
            "created_at": item.created_at,
            "size_bytes": item.size_bytes,
            "total_files": item.total_files,
            "sha256": item.sha256,
        })
    return _ok("backup", rows)


def _split_line(offset: int, line: str) -> tuple:
    if "=" in line:
        key, value = line.split("=", 1)
    elif ":" in line:
        key, value = line.split(":", 1)
    else:
        key, value = "<line %d>" % offset, line
    return key.strip(), value.strip()


def _conf_row(name: str, offset: int, raw: str):
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    key, value = _split_line(offset, line)
    return {"file": name, "key": key, "value": value}


def config_rows(base: Path) -> Section:
    """逐行展开 /etc/agentrt 下的配置文件。"""
    folder = base / CONF_DIR
    if not folder.is_dir():
        return _down("config", "缺少配置目录: " + CONF_DIR)
    rows = []
    for path in sorted(folder.glob("*.conf"), key=lambda item: item.name):
        text = _read(path)
        if text is None:
            rows.append({"file": path.name, "key": "<unreadable>",
                         "value": "权限不足或文件损坏"})
            continue
        for offset, raw in enumerate(text.splitlines(), 1):
            row = _conf_row(path.name, offset, raw)
            if row is not None:
                rows.append(row)
    return _ok("config", rows)
