# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 记忆层的事实统计
#
# 职责：只报告磁盘上可复核的量——块数、字节数、存活项数、块内利用率、碎片
# 率、索引占比与归档占用，供 status/stats 与 defrag 判定共用。
#
# 边界：不编造 evict 次数、命中率一类依赖 mem_d 运行时计数器的指标；那需要
# 守护进程导出的计数器，本版命令行不连接守护进程，因此不提供。容量利用率
# 同理，需要配额来源，这里只报「块内已用/总块字节」。
#
# 依赖：store.py（布局与读取）、model.py（编码后尺寸）、标准库 os/pathlib。

"""AirymaxOS memory level statistics from on-disk facts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .model import BadStore, Level, parse_level
from .store import DATA, Store

__all__ = ["agent_status", "archive_bytes", "frag_pct", "index_ratio",
           "level_stats"]

LOW_UTIL = 70.0
INDEX_SHARE = 10.0


def frag_pct(live: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(max(total - live, 0) * 100.0 / total, 2)


def index_ratio(index_bytes: int, data_bytes: int) -> float:
    if data_bytes <= 0:
        return 0.0
    return round(index_bytes * 100.0 / data_bytes, 2)


def _files(folder: Path) -> List[Path]:
    if folder.is_symlink() or not folder.is_dir():
        return []
    return [path for path in folder.iterdir()
            if path.is_file() and not path.is_symlink()]


def archive_bytes(store: Store, ident: Any) -> int:
    data = store.datadir(Level.L4, ident, archived=True)
    return sum(path.stat().st_size for path in _files(data))


def _empty(store: Store, rank: Level, ident: Any) -> Dict[str, Any]:
    return {
        "agent": str(ident),
        "level": rank.value,
        "exists": False,
        "items": 0,
        "blocks": 0,
        "data_bytes": 0,
        "live_bytes": 0,
        "dead_items": 0,
        "dead_bytes": 0,
        "index_bytes": 0,
        "index_ratio_pct": 0.0,
        "utilization_pct": 0.0,
        "fragmentation_pct": 0.0,
        "checksum": "missing",
        "block_kb": store.block_kb,
        "owner": "",
        "updated_at": "",
    }


def _count(blocks: Any) -> List[int]:
    live_items = dead_items = live_bytes = dead_bytes = 0
    for rows in blocks.values():
        for rec in rows:
            size = rec.size()
            if rec.deleted:
                dead_items += 1
                dead_bytes += size
            else:
                live_items += 1
                live_bytes += size
    return [live_items, dead_items, live_bytes, dead_bytes]


def level_stats(store: Store, level: Any, ident: Any,
                archived: bool = False) -> Dict[str, Any]:
    rank = parse_level(level)
    empty = _empty(store, rank, ident)
    if not store.exists(rank, ident, archived):
        return empty
    meta = store.meta(rank, ident, archived)
    blocks = store.read_blocks(rank, ident, archived)
    folder = store.folder(rank, ident, archived)
    data_bytes = sum(path.stat().st_size for path in _files(folder / DATA))
    live_items, dead_items, live_bytes, dead_bytes = _count(blocks)
    index_path = folder / "index.airyidx"
    index_size = index_path.stat().st_size if index_path.is_file() else 0
    record = dict(empty)
    record.update({
        "exists": True,
        "items": live_items,
        "blocks": len(blocks),
        "data_bytes": data_bytes,
        "live_bytes": live_bytes,
        "dead_items": dead_items,
        "dead_bytes": dead_bytes,
        "index_bytes": index_size,
        "index_ratio_pct": index_ratio(index_size, data_bytes),
        "fragmentation_pct": frag_pct(data_bytes - dead_bytes, data_bytes),
        "checksum": store.verify(rank, ident, archived),
        "block_kb": int(meta.get("block_kb") or store.block_kb),
        "owner": str(meta.get("owner") or ""),
        "updated_at": str(meta.get("updated_at") or ""),
    })
    record["utilization_pct"] = round(
        live_bytes * 100.0 / data_bytes, 2) if data_bytes else 0.0
    return record


def agent_status(store: Store, ident: Any,
                 levels: Optional[List[str]] = None) -> Dict[str, Any]:
    ranks = [parse_level(item) for item in (levels or ["L3", "L4"])]
    report: Dict[str, Any] = {"agent": str(ident), "levels": {}}
    for rank in ranks:
        report["levels"][rank.value] = level_stats(store, rank, ident)
    report["archive_bytes"] = archive_bytes(store, ident)
    report["exists"] = any(row["exists"] for row in report["levels"].values())
    return report


def missing(report: Dict[str, Any]) -> bool:
    return not report.get("exists")


def require(report: Dict[str, Any]) -> None:
    if missing(report):
        raise BadStore(f"记忆仓库不存在: {report['agent']}")
