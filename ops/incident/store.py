# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 事件台账持久化
#
# 一条事件一个 JSON 文件，编号即文件名，读者按编号定位不需要索引；
# 列表靠目录扫描，量级是运维事件的量级，不引入数据库。写盘一律走
# 临时文件加 rename 并 fsync 目录，掉电后不会看到半份记录。

"""Filesystem backed incident ledger."""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from airylib.err import need
from airylib.ledger import FILE_MODE, atomic_write, compact

from .model import (NS, TERMINAL, BadIncident, BadInput, BadStore,
                    Category, Incident, Severity, State, check_id, day_key,
                    iso, make_id, open_incident)

DIR_MODE = 0o750
LOCK = ".lock"
ARCHIVE = "archive"
REPORT = "postmortem"
DAY = 86400 * NS
_PREFIX = "INC-"


def _closed_in(inc: Incident, month: str) -> bool:
    """终态且已恢复、且恢复月份匹配的记录才计入当月统计。"""
    if inc.status not in TERMINAL or not inc.resolved_at:
        return False
    return time.strftime("%Y%m", time.gmtime(inc.resolved_at / NS)) == month


def _open(rows: list[Incident]) -> int:
    return sum(1 for inc in rows if inc.status not in TERMINAL)


class Store:
    """Incident ledger rooted at a directory."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.archive = self.root / ARCHIVE
        self.reports = self.root / REPORT

    def _ensure(self) -> None:
        for path in (self.root, self.archive, self.reports):
            path.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)

    def _write(self, path: Path, text: str) -> None:
        self._ensure()
        atomic_write(path, text)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self._ensure()
        path = self.root / LOCK
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _path(self, ident: str) -> Path:
        return self.root / f"{check_id(ident)}.json"

    def save(self, inc: Incident) -> None:
        self._write(self._path(inc.incident_id), compact(inc.to_json()) + "\n")

    def load(self, ident: str) -> Incident:
        path = self._path(ident)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise BadStore(f"事件不存在: {ident}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise BadStore(f"{path}: {exc}") from exc
        try:
            return Incident.from_json(doc)
        except BadIncident as exc:
            raise BadStore(f"{path}: {exc}") from exc

    def ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(path.stem for path in self.root.glob(f"{_PREFIX}*.json"))

    def rows(self) -> list[Incident]:
        return [self.load(ident) for ident in self.ids()]

    def list(self, state: Any = None, severity: Any = None,
             limit: int | None = 200) -> list[Incident]:
        want = State.parse(state) if state else None
        level = Severity.parse(severity) if severity else None
        rows = [inc for inc in self.rows()
                if (want is None or inc.status is want)
                and (level is None or inc.severity is level)]
        rows.sort(key=lambda inc: (inc.severity.rank, -inc.detected_at))
        return rows if limit is None else rows[:max(limit, 0)]

    def _next(self, day: str) -> int:
        top = 0
        for path in self.root.glob(f"{_PREFIX}{day}-*.json"):
            tail = path.stem.rsplit("-", 1)[-1]
            if tail.isdigit():
                top = max(top, int(tail))
        if top >= 999:
            raise BadStore(f"{day} 的事件编号已用满")
        return top + 1

    def create(self, stamp: int, severity: Any, category: Any, title: str,
               actor: str, source: str = "", detail: str = "",
               alerts: Any = (), agents: Any = (),
               daemons: Any = ()) -> Incident:
        self._ensure()
        with self._lock():
            day = day_key(stamp)
            inc = open_incident(
                make_id(day, self._next(day)), severity, category, title,
                stamp, actor, source=source, detail=detail, alerts=alerts,
                agents=agents, daemons=daemons,
            )
            self.save(inc)
            return inc

    def find_alert(self, alert_id: str) -> Incident | None:
        for inc in self.rows():
            if alert_id in inc.alerts:
                return inc
        return None

    def report(self, inc: Incident, text: str) -> Path:
        self._ensure()
        path = self.reports / f"PM-{inc.incident_id[len(_PREFIX):]}.md"
        self._write(path, text)
        return path

    def prune(self, days: int, stamp: int, apply: bool = False,
              sec_days: int = 365
              ) -> tuple[list[Incident], list[str]]:
        """按保留期挑出可归档记录；安全事件另有更长的保留期。"""
        if days < 1 or sec_days < 1:
            raise BadInput("保留天数必须 >= 1")
        picked = self._expired(stamp - days * DAY, stamp - sec_days * DAY)
        if not apply:
            return picked, []
        return picked, self._retire(picked, stamp)

    def _expired(self, plain: int, safe: int) -> list[Incident]:
        """已收尾且越过各自保留期的记录；安全类按更长的界。"""
        return [inc for inc in self.rows()
                if inc.status in TERMINAL
                and (inc.resolved_at or inc.detected_at)
                < (safe if inc.category is Category.SECURITY else plain)]

    def _retire(self, picked: list[Incident], stamp: int) -> list[str]:
        moved: list[str] = []
        for inc in picked:
            self._archive(inc, stamp)
            self._path(inc.incident_id).unlink()
            moved.append(inc.incident_id)
        return moved

    def _archive(self, inc: Incident, stamp: int) -> None:
        self._ensure()
        base = inc.resolved_at or inc.detected_at
        month = time.strftime("%Y%m", time.gmtime(base / NS))
        row = inc.to_json()
        row["archived_at"] = iso(stamp)
        report = self.reports / f"PM-{inc.incident_id[len(_PREFIX):]}.md"
        if report.is_file():
            row["postmortem_md"] = report.read_text(encoding="utf-8")
            report.unlink()
        path = self.archive / f"{month}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(compact(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, FILE_MODE)

    def stats(self, month: str, stamp: int) -> dict[str, Any]:
        need(len(month) == 6 and month.isdigit(),
             f"月份格式应为 YYYYMM: {month}", BadInput)
        rows = self.rows()
        closed = [inc for inc in rows if _closed_in(inc, month)]
        spans = [inc.span(inc.resolved_at) for inc in closed]
        return {
            "month": month,
            "total": len(rows),
            "open": _open(rows),
            "closed": len(closed),
            "mttr_s": round(sum(spans) / len(spans) / NS, 1) if spans else None,
            "actions": sum(len(inc.response_actions) for inc in closed),
            "by_state": dict(Counter(inc.status.value for inc in rows)),
            "by_severity": dict(Counter(inc.severity.value for inc in rows)),
        }
