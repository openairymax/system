# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 三处静态计数的快照
#
# 判据要回答「内核到底动没动」：cap_used 是能力检查命中次数，airy_stc
# 是调度策略分发计数，policy 表是每张决策表的求值分布。三者形状不同，
# 但都必须区分「节点不存在」与「值为零」——前者是本机没有这条控制面，
# 后者是控制面存在但尚未触发，归因完全不同。
#
# 每个解析器只接受内核实际打印的列数，多一列少一列都判为 malformed，
# 避免上游格式变更后把错位的数字当成计数继续上报。

"""AirymaxOS static counter snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import files
from .paths import Paths

__all__ = ["Stats", "Table", "snapshot"]

READABLE = "readable"

UNAVAILABLE = "unavailable"

HEADER = "#"

TABLE_FIELDS = 9

STC_TOTAL = "total"


@dataclass(frozen=True, slots=True)
class Table:
    """policy 表里的一行，列序与内核 seq_printf 一致。"""

    name: str
    hook: str
    rows: int
    evals: int
    allow: int
    deny: int
    audit: int
    complain: int
    fault: int


@dataclass(frozen=True, slots=True)
class Stats:
    """一次快照；缺失项一律为 None，不用 0 冒充。"""

    cap_used: int | None
    stc_total: int | None
    stc_policies: tuple[tuple[str, int], ...]
    tables: tuple[Table, ...]
    policy: str
    policy_reason: str | None

    @property
    def facts(self) -> bool:
        """三处静态计数是否都已读到。"""
        return (
            self.cap_used is not None
            and self.stc_total is not None
            and self.policy == READABLE
        )


def snapshot(paths: Paths) -> Stats:
    """读取三处计数；任何一处不可读都不影响其余两处。"""
    body = files.text(Path(paths.policy))
    total, policies = _stc(files.text(Path(paths.stc)))
    return Stats(
        cap_used=files.num(Path(paths.cap)),
        stc_total=total,
        stc_policies=policies,
        tables=_tables(body),
        policy=READABLE if body is not None else UNAVAILABLE,
        policy_reason=None if body is not None else _why(Path(paths.policy)),
    )


def _why(path: Path) -> str:
    return "missing" if files.mode(path) is None else "unreadable"


def _tables(body: str | None) -> tuple[Table, ...]:
    if body is None:
        return ()
    rows = (_table(line) for line in body.splitlines())
    return tuple(row for row in rows if row is not None)


def _table(line: str) -> Table | None:
    """解析一行决策表；列数不符或数字非法即丢弃该行。"""
    text = line.strip()
    if not text or text.startswith(HEADER):
        return None
    parts = text.split()
    if len(parts) != TABLE_FIELDS:
        return None
    try:
        values = [int(item, 10) for item in parts[2:]]
    except ValueError:
        return None
    return Table(parts[0], parts[1], *values)


def _stc(body: str | None) -> tuple[int | None, tuple[tuple[str, int], ...]]:
    """拆分 stc 统计：首行是分发总数，其余是各策略的计数。"""
    if body is None:
        return None, ()
    total = None
    policies: list[tuple[str, int]] = []
    for line in body.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            count = int(parts[1], 10)
        except ValueError:
            continue
        if parts[0] == STC_TOTAL:
            total = count
        else:
            policies.append((parts[0], count))
    return total, tuple(policies)