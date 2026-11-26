# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS Agent 元数据持久化
#
# 一个 Agent 一个目录，元数据落在 <root>/<id>/meta.json，列表靠目录扫描。
# 写盘走同目录临时文件 + fsync + rename，并持文件锁串行化编号分配与写入，
# 掉电或两个运维终端同时操作都不会看到半份记录。归档目录与数据目录同级，
# 形如 /var/lib/agentrt/agents.archive/<id>-<UTC 时间>/。

"""Filesystem backed Agent metadata store."""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from airylib.err import need
from airylib.ledger import atomic_write, compact

from .model import (BadAgent, BadStore, NS, Agent, check_id, need_mem,
                    need_sched, now_ns)

ROOT = "/var/lib/agentrt/agents"
DIR_MODE = 0o750
LOCK = ".lock"
META = "meta.json"


def _digits(name: str) -> int:
    return int(name) if name.isdigit() and int(name) > 0 else 0


class Store:
    """Agent metadata rooted at a directory."""

    def __init__(self, root: str | Path | None = None) -> None:
        chosen = root or os.environ.get("AIRY_AGENT_ROOT") or ROOT
        self.root = Path(chosen)
        self.archive = self.root.parent / f"{self.root.name}.archive"

    def _ensure(self) -> None:
        for path in (self.root, self.archive):
            path.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self._ensure()
        with (self.root / LOCK).open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield

    def _path(self, ident: str) -> Path:
        return self.root / check_id(ident) / META

    def _write(self, agent: Agent) -> None:
        path = self._path(agent.id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        atomic_write(path, compact(agent.to_json()) + "\n")

    def _scan(self, base: Path) -> set[int]:
        if not base.is_dir():
            return set()
        out: set[int] = set()
        for entry in base.iterdir():
            head = entry.name.split("-", 1)[0]
            number = _digits(head)
            if number and (base is not self.root or entry.is_dir()):
                out.add(number)
        return out

    def next_id(self) -> str:
        used = self._scan(self.root) | self._scan(self.archive)
        return str(max(used, default=0) + 1)

    def ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        found = [entry.name for entry in self.root.iterdir()
                 if entry.is_dir() and _digits(entry.name)
                 and (entry / META).is_file()]
        return sorted(found, key=int)

    def load(self, ident: str) -> Agent:
        path = self._path(ident)
        need(path.is_file(), f"Agent {ident} 不存在", BadStore)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            return Agent.from_json(doc)
        except BadStore:
            raise
        except (BadAgent, ValueError, OSError, json.JSONDecodeError) as exc:
            raise BadStore(f"Agent {ident} 的 meta.json 损坏: {exc}") from exc

    def rows(self, state: str | None = None) -> list[Agent]:
        agents = [self.load(ident) for ident in self.ids()]
        if state:
            agents = [item for item in agents if item.state.value == state]
        return sorted(agents, key=lambda item: (item.created_at, int(item.id)))

    def create(self, cfg: Any, opts: dict[str, Any],
               stamp: int = 0) -> Agent:
        at = stamp or now_ns()
        sched = {
            "runtime_ms": opts.get("runtime_ms", cfg["default_sched_runtime_ms"]),
            "deadline_ms": opts.get("deadline_ms",
                                    cfg["default_sched_deadline_ms"]),
            "period_ms": opts.get("period_ms", cfg["default_sched_period_ms"]),
        }
        memory = {
            "l1_mb": opts.get("l1_mb", cfg["default_l1_mb"]),
            "l2_mb": opts.get("l2_mb", cfg["default_l2_mb"]),
        }
        need_sched(sched)
        need_mem(memory)
        with self._lock():
            ident = self.next_id()
            agent = Agent(
                id=ident,
                name=opts["name"],
                template=opts["template"],
                state="INACTIVE",
                created_at=at,
                updated_at=at,
                token={
                    "total_budget": opts.get("token_budget",
                                             cfg["default_token_budget"]),
                    "consumed": 0,
                    "reset_period": "manual",
                    "last_reset_at": "",
                },
                memory=memory,
                sched=sched,
                capabilities=list(opts.get("capabilities") or []),
            )
            agent.audit("create", at)
            self._write(agent)
            return agent

    def save(self, agent: Agent) -> None:
        with self._lock():
            self._write(agent)

    def archive_agent(self, agent: Agent, stamp: int = 0) -> Path:
        at = stamp or now_ns()
        name = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(at / NS))
        with self._lock():
            target = self.archive / f"{agent.id}-{name}"
            counter = 1
            while target.exists():
                target = self.archive / f"{agent.id}-{name}.{counter}"
                counter += 1
            source = self.root / agent.id
            need(source.is_dir(), f"Agent {agent.id} 的目录不存在", BadStore)
            os.replace(source, target)
            return target
