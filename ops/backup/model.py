# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.

"""备份台账的数据模型：编号、清单、保留期与路径安全。

这里不碰文件系统，只把「一次备份是什么」定死：编号里的日期与序号、
清单里的摘要与体积、保留期按哪种日历推进。存储层与命令行都读同一份
定义，避免三处各自解释一个字段。
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable

NS = 1_000_000_000
DAY = 86400 * NS
SCHEMA = 1
VERSION = "0.0.5"

_ID_RE = re.compile(r"^(FULL|INC|WEEK|MONTH)-(\d{8})-(\d{3,6})$")


class BadBackup(Exception):
    """备份子系统的基类异常；code 与命令行退出码一致。"""

    code = 1


class BadInput(BadBackup):
    """调用方参数不成立（未知编号、非法来源、未确认的全量恢复）。"""

    code = 2


class BadStore(BadBackup):
    """仓库或台账不可读、不可写、缺件。"""

    code = 3


class BadIntegrity(BadBackup):
    """摘要、体积、签名或归档结构不符；恢复必须停在这里。"""

    code = 4


class Kind(str, Enum):
    FULL = "full"
    INCREMENTAL = "incremental"
    WEEKLY = "weekly"
    MONTHLY = "monthly"

    @property
    def prefix(self) -> str:
        return {
            Kind.FULL: "FULL",
            Kind.INCREMENTAL: "INC",
            Kind.WEEKLY: "WEEK",
            Kind.MONTHLY: "MONTH",
        }[self]

    @classmethod
    def parse(cls, value: str | "Kind") -> "Kind":
        if isinstance(value, Kind):
            return value
        text = str(value).strip().lower()
        for kind in cls:
            if text in (kind.value, kind.name.lower()):
                return kind
        raise BadInput(f"未知备份类型: {value}")


_PREFIX_KIND = {k.prefix: k for k in Kind}


def iso(stamp_ns: int) -> str:
    """纳秒时间戳 → UTC 秒级 ISO 字符串（清单的唯一时间写法）。"""
    seconds, _ = divmod(int(stamp_ns), NS)
    return datetime.fromtimestamp(seconds, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> int:
    """清单里的 ISO 字符串 → 纳秒时间戳。"""
    try:
        dt = datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise BadStore(f"时间格式不合法: {text!r}") from exc
    return int(dt.replace(tzinfo=timezone.utc).timestamp()) * NS


def day_key(stamp_ns: int) -> str:
    return iso(stamp_ns)[:10].replace("-", "")


def make_id(kind: Kind, stamp_ns: int, seq: int) -> str:
    if seq < 1:
        raise BadInput("备份序号必须从 1 开始")
    return f"{Kind.parse(kind).prefix}-{day_key(stamp_ns)}-{seq:03d}"


def check_id(backup_id: str) -> str:
    match = _ID_RE.match(str(backup_id))
    if not match:
        raise BadInput(f"备份编号不合法: {backup_id!r}")
    return backup_id


def parse_id(backup_id: str) -> tuple[Kind, str, int]:
    check_id(backup_id)
    prefix, day, seq = _ID_RE.match(backup_id).groups()  # type: ignore[union-attr]
    return _PREFIX_KIND[prefix], day, int(seq)


def safe_arcname(name: str) -> str:
    """归档内路径只允许相对、无 ..、无空段。"""
    text = str(name).replace("\\", "/").strip()
    if not text or text.startswith("/"):
        raise BadInput(f"归档路径必须是相对路径: {name!r}")
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise BadInput(f"归档路径含空段或上跳: {name!r}")
    return "/".join(parts)


def member_under_agent(name: str, agent_id: str | int) -> bool:
    """判断归档成员是否属于某个 Agent 的私有数据。

    覆盖三类落点：主机 Agent 目录、L3 记忆、L4 记忆。调用方传 agent 号
    时只还原该号；不传则整包还原。
    """
    parts = str(name).strip("/").split("/")
    if parts[0] == "var" and len(parts) >= 5 and parts[1] == "lib" \
            and parts[2] == "airy":
        rest = parts[3:]
    else:
        rest = parts
    if len(rest) >= 3 and rest[0] == "agents":
        return rest[1] == str(agent_id)
    if len(rest) >= 4 and rest[0] == "memory" and rest[1] in ("l3", "l4"):
        return rest[2] == str(agent_id)
    return False


def _minus_months(stamp_ns: int, months: int) -> int:
    seconds, _ = divmod(int(stamp_ns), NS)
    dt = datetime.fromtimestamp(seconds, timezone.utc)
    total = (dt.year * 12 + dt.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    shifted = dt.replace(year=year, month=month, day=day)
    return int(shifted.timestamp()) * NS


@dataclass(frozen=True)
class Retention:
    """保留期；分钟粒度的时间推进交给调用方，这里只按日历算截止点。"""

    incremental_days: int = 7
    full_days: int = 30
    weekly_weeks: int = 12
    monthly_months: int = 12

    def cutoff(self, kind: Kind, now_ns: int) -> int:
        kind = Kind.parse(kind)
        if kind is Kind.INCREMENTAL:
            return now_ns - self.incremental_days * DAY
        if kind is Kind.FULL:
            return now_ns - self.full_days * DAY
        if kind is Kind.WEEKLY:
            return now_ns - self.weekly_weeks * 7 * DAY
        return _minus_months(now_ns, self.monthly_months)


@dataclass
class BackupRecord:
    """一次备份的清单主体；字段与 06-backup-recovery.md §3.5 对齐。"""

    backup_id: str
    kind: Kind
    created_at: int
    completed_at: int
    file: str
    compression: str
    size_bytes: int
    sha256: str
    actor: str = ""
    signed_by: str = ""
    agent_count: int = 0
    config_files: int = 0
    total_files: int = 0
    sources: list[dict[str, Any]] = field(default_factory=list)
    previous_backup_id: str = ""
    version: str = VERSION
    schema: int = SCHEMA

    def __post_init__(self) -> None:
        self.backup_id = check_id(self.backup_id)
        self.kind = Kind.parse(self.kind)
        self.created_at = int(self.created_at)
        self.completed_at = int(self.completed_at)
        self.size_bytes = int(self.size_bytes)
        self.sha256 = str(self.sha256)
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise BadStore(f"摘要不是 64 位小写十六进制: {self.sha256!r}")
        if not self.file or "/" in self.file or self.file in (".", ".."):
            raise BadStore(f"归档文件名不合法: {self.file!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "version": self.version,
            "backup_id": self.backup_id,
            "kind": self.kind.value,
            "created_at": iso(self.created_at),
            "completed_at": iso(self.completed_at),
            "file": self.file,
            "compression": self.compression,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "actor": self.actor,
            "signed_by": self.signed_by,
            "agent_count": self.agent_count,
            "config_files": self.config_files,
            "total_files": self.total_files,
            "sources": list(self.sources),
            "previous_backup_id": self.previous_backup_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BackupRecord":
        try:
            return cls(
                backup_id=data["backup_id"],
                kind=data["kind"],
                created_at=parse_iso(data["created_at"]),
                completed_at=parse_iso(data["completed_at"]),
                file=data["file"],
                compression=data.get("compression", "zstd"),
                size_bytes=data["size_bytes"],
                sha256=data["sha256"],
                actor=data.get("actor", ""),
                signed_by=data.get("signed_by", ""),
                agent_count=int(data.get("agent_count", 0)),
                config_files=int(data.get("config_files", 0)),
                total_files=int(data.get("total_files", 0)),
                sources=list(data.get("sources", [])),
                previous_backup_id=data.get("previous_backup_id", ""),
                version=data.get("version", VERSION),
                schema=int(data.get("schema", SCHEMA)),
            )
        except KeyError as exc:
            raise BadStore(f"清单缺字段: {exc.args[0]}") from exc


def summarize_sources(entries: Iterable[dict[str, Any]]) -> tuple[int, int, int]:
    """返回 (文件总数, 字节总数, Agent 数)。

    Agent 数只从名为 agents 的来源数直接子目录，避免把记忆层里的同名
    子目录重复计入。
    """
    files = 0
    size = 0
    agents = 0
    for entry in entries:
        files += int(entry.get("files", 0))
        size += int(entry.get("bytes", 0))
        if str(entry.get("name", "")).rstrip("/").endswith("agents"):
            agents += int(entry.get("agent_count", 0))
    return files, size, agents
