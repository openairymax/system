# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 记忆安全擦除
#
# 职责：实现设计 §8.2 的三级可执行擦除——quick 仅 unlink/去索引、overwrite
# 零填充、secure 按 DoD 5220.22-M 三遍（0x00、0xFF、随机）后 unlink；每次
# 擦除都落审计与状态，覆写函数独立可测。
#
# 边界：crypto 级需要 L4 加密存储与 sec_d 密钥托管，二者都在数据面，本版
# 没有交付，必须显式报错（退出码 3）而不是假装成功。擦除以块为最小单位，
# 同块内的其他记忆项会被完整重写；不承诺 SSD 磨损均衡层之上的物理不可恢复。
#
# 依赖：store.py（锁、块读写、删除）、audit.py（审计追加）、model.py（方法校验）。

"""AirymaxOS memory erase levels and audit integration."""

from __future__ import annotations

import errno
import getpass
import json
import os
import random
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from airylib.err import need
from airylib.ledger import atomic_write

from . import audit
from .model import (
    BadInput,
    BadPerm,
    BadState,
    BadStore,
    Method,
    Record,
    check_id,
    iso,
    now_ns,
    parse_items,
    parse_level,
    parse_method,
    unsupported,
)
from .store import Store

__all__ = ["erase", "overwrite_file", "status", "trail"]

CHUNK = 64 * 1024
STATE = "state"
FILE = "erase.json"
Fill = Callable[[int], bytes]


def zero_fill(size: int) -> bytes:
    return b"\x00" * size


def one_fill(size: int) -> bytes:
    return b"\xff" * size


def random_fill(size: int, rng: Optional[random.Random] = None) -> bytes:
    return (rng or random.SystemRandom()).randbytes(size)


def overwrite_file(path: Path, fills: List[Fill]) -> int:
    """按给定填充序列逐遍覆写并 fsync，返回实际写入字节数。"""
    need(bool(fills), "覆写至少需要一遍填充", BadInput)
    fd = -1
    try:
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
        size = os.fstat(fd).st_size
        for fill in fills:
            os.lseek(fd, 0, os.SEEK_SET)
            done = 0
            while done < size:
                part = fill(min(CHUNK, size - done))
                os.write(fd, part)
                done += len(part)
            os.fsync(fd)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM):
            raise BadPerm(f"无权覆写: {path}") from exc
        raise BadStore(f"覆写失败: {path}: {exc}") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    return size * len(fills)


def _fills(method: Method) -> List[Fill]:
    if method is Method.QUICK:
        return []
    if method is Method.OVERWRITE:
        return [zero_fill]
    return [zero_fill, one_fill, random_fill]


def _select(blocks: Dict[str, List[Record]],
            ids: Optional[List[str]]) -> Any:
    live = {rec.item.id for rows in blocks.values()
            for rec in rows if not rec.deleted}
    chosen = set(live) if ids is None else set(ids)
    missing = sorted(chosen - live)
    need(not missing, f"记忆项不存在: {','.join(missing)}", BadState)
    targets = sorted({
        name for name, rows in blocks.items()
        if any(not rec.deleted and rec.item.id in chosen for rec in rows)})
    return chosen, targets


def _wipe(blocks: Dict[str, List[Record]], chosen: set, folder: Path,
          fills: List[Fill]) -> int:
    written = 0
    for name, rows in blocks.items():
        if any(not rec.deleted and rec.item.id in chosen for rec in rows):
            written += overwrite_file(folder / name, fills)
    return written


def _shape(blocks: Dict[str, List[Record]], chosen: set,
           fills: List[Fill]) -> Dict[str, List[Record]]:
    shape: Dict[str, List[Record]] = {}
    for name, rows in blocks.items():
        kept = []
        for rec in rows:
            if not rec.deleted and rec.item.id in chosen:
                if not fills:
                    kept.append(Record(rec.item, True))
            else:
                kept.append(rec)
        if kept:
            shape[name] = kept
    return shape


def _prune(blocks: Dict[str, List[Record]], shape: Dict[str, List[Record]],
           folder: Path) -> None:
    for name in blocks:
        path = folder / name
        if name not in shape and path.exists():
            path.unlink()


def _partial(store: Store, level: Any, ident: Any, blocks: Dict[str, List[Record]],
             chosen: set, fills: List[Fill]) -> Dict[str, Any]:
    folder = store.datadir(level, ident)
    written = _wipe(blocks, chosen, folder, fills) if fills else 0
    shape = _shape(blocks, chosen, fills)
    _prune(blocks, shape, folder)
    store.rewrite(level, ident, shape)
    return {"blocks": len(blocks), "bytes": written}


def _whole(store: Store, level: Any, ident: Any, blocks: Dict[str, List[Record]],
           fills: List[Fill]) -> Dict[str, Any]:
    folder = store.datadir(level, ident)
    written = 0
    if fills:
        for name in sorted(blocks):
            written += overwrite_file(folder / name, fills)
    files, freed = store.drop(level, ident)
    return {"blocks": len(blocks), "files": files,
            "bytes": max(written, freed)}


def _operator() -> str:
    text = os.environ.get("AIRY_MEMORY_OPERATOR") or ""
    if text:
        return text
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return "unknown"


def _save_state(store: Store, doc: Dict[str, Any]) -> None:
    folder = store.root / STATE
    folder.mkdir(parents=True, exist_ok=True, mode=0o750)
    atomic_write(folder / FILE,
                 json.dumps(doc, ensure_ascii=False, indent=2,
                            sort_keys=True) + "\n")


def erase(store: Store, cfg: Dict[str, Any], opts: Dict[str, Any]) -> Dict[str, Any]:
    method = parse_method(opts.get("method") or cfg["erase_default_level"])
    unsupported(method)
    rank = parse_level(opts.get("level") or "L3")
    ident = check_id(opts.get("agent"))
    ids = parse_items(opts["items"]) if opts.get("items") else None
    need(ids is not None or bool(opts.get("all")),
         "擦除需要 --items 指定记忆项或 --all 覆盖整个容器", BadInput)
    started = time.monotonic()
    fills = _fills(method)
    with store.locked():
        need(store.exists(rank, ident), f"记忆仓库不存在: {ident}", BadStore)
        blocks = store.read_blocks(rank, ident)
        chosen, _targets = _select(blocks, ids)
        if ids is None:
            stats_row = _whole(store, rank, ident, blocks, fills)
        else:
            stats_row = _partial(store, rank, ident, blocks, chosen, fills)
    row = {
        "ts": iso(now_ns()),
        "operator": _operator(),
        "action": "erase",
        "agent": ident,
        "level": rank.value,
        "method": method.value,
        "passes": len(fills),
        "scope": "agent" if ids is None else "items",
        "items": len(chosen),
        "ids": sorted(chosen) if ids is not None else "*",
        "blocks": stats_row["blocks"],
        "bytes": stats_row["bytes"],
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
    _save_state(store, row)
    if cfg.get("erase_audit", True):
        audit.append(row)
    return row


def status(store: Store) -> Dict[str, Any]:
    path = store.root / STATE / FILE
    if not path.is_file():
        return {"state": "none"}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadStore(f"擦除状态文件损坏: {path}") from exc
    need(isinstance(doc, dict), "擦除状态必须是对象", BadStore)
    return doc


def trail(limit: int = 50) -> List[Dict[str, Any]]:
    return audit.read(limit)
