# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 记忆仓库（/var/lib/airy/memory）
#
# 职责：按设计 §3 的布局落盘——l3/<agent>/、l4/<agent>/、l4.archive/<agent>/
# 三个命名空间，每个容器内含 data/NNNNNN.airyblk、index.airyidx、meta.json
# 与 checksum.sha256；提供原子写、flock 互斥、索引重建与校验和核验。
#
# 边界：只回答「磁盘上有什么」，不定义碎片阈值与整理策略（defrag.py）、
# 不做迁移决策（transfer.py）、不执行擦除（erase.py）。所有写操作要求调用方
# 先持有 locked()，公开的 save_items/remove_items 只是便捷包装。
#
# 依赖：model.py 的块格式与校验、airylib.ledger.atomic_write、标准库
# fcntl/hashlib/json/os/struct。

"""AirymaxOS memory repository: layout, blocks, index and locking."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from airylib.err import need
from airylib.ledger import atomic_write

from .model import (
    BLOCK_HEAD,
    SCHEMA,
    BadInput,
    BadPerm,
    BadState,
    BadStore,
    Item,
    Level,
    Record,
    block_name,
    check_block,
    check_id,
    check_item,
    decode_block,
    encode_block,
    encode_rec,
    iso,
    now_ns,
    parse_level,
)

__all__ = ["Store"]

DATA = "data"
INDEX = "index.airyidx"
META = "meta.json"
CHECKSUM = "checksum.sha256"
LOCK = ".lock"
STAGING = "data.new"
RETIRED = "data.old"
ARCHIVE = "l4.archive"
DEFAULT_ROOT = "/var/lib/airy/memory"
DIR_MODE = 0o750
FILE_MODE = 0o640


class Store:
    """以内存根为边界的记忆仓库；root 可由 AIRY_MEMORY_ROOT 覆盖。"""

    def __init__(self, root: Optional[str] = None, block_kb: int = 4096) -> None:
        chosen = root or os.environ.get("AIRY_MEMORY_ROOT") or DEFAULT_ROOT
        self.root = Path(chosen)
        need(isinstance(block_kb, int) and block_kb > 0,
             f"数据块大小必须为正整数 KB: {block_kb!r}", BadInput)
        self.block_kb = block_kb

    def folder(self, level: Any, ident: Any,
               archived: bool = False) -> Path:
        rank = parse_level(level)
        agent = check_id(ident)
        if archived:
            need(rank is Level.L4, "只有 L4 存在归档层", BadInput)
            return self.root / ARCHIVE / agent
        return self.root / rank.value.lower() / agent

    def datadir(self, level: Any, ident: Any,
                archived: bool = False) -> Path:
        return self.folder(level, ident, archived) / DATA

    def exists(self, level: Any, ident: Any,
               archived: bool = False) -> bool:
        return (self.folder(level, ident, archived) / META).is_file()

    def ids(self, level: Any) -> List[str]:
        base = self.root / parse_level(level).value.lower()
        if not base.is_dir():
            return []
        return sorted(
            entry.name for entry in base.iterdir()
            if entry.name.isdigit() and (entry / META).is_file())

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        handle = (self.root / LOCK).open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def meta(self, level: Any, ident: Any,
             archived: bool = False) -> Dict[str, Any]:
        folder = self._guard(self.folder(level, ident, archived))
        doc = self._read_json(folder / META, "元数据")
        need(doc.get("schema") == SCHEMA, f"元数据版本不受支持: {ident}",
             BadStore)
        need(doc.get("level") == parse_level(level).value,
             f"元数据层级不一致: {ident}", BadStore)
        need(doc.get("agent") == check_id(ident),
             f"元数据属主标识不一致: {ident}", BadStore)
        return doc

    def owner(self, level: Any, ident: Any, archived: bool = False) -> str:
        return str(self.meta(level, ident, archived).get("owner") or "")

    def read_blocks(self, level: Any, ident: Any,
                    archived: bool = False) -> Dict[str, List[Record]]:
        folder = self._guard(self.folder(level, ident, archived))
        need((folder / META).is_file(), f"记忆仓库不存在: {ident}", BadStore)
        data = folder / DATA
        need(data.is_dir() and not data.is_symlink(),
             f"数据目录缺失: {ident}", BadStore)
        out: Dict[str, List[Record]] = {}
        for path in sorted(data.iterdir(), key=lambda item: item.name):
            if not path.name.endswith(".airyblk"):
                continue
            name = check_block(path.name)
            out[name] = decode_block(self._read(path))
        return out

    def items(self, level: Any, ident: Any,
              archived: bool = False) -> List[Item]:
        rows: List[Item] = []
        for records in self.read_blocks(level, ident, archived).values():
            rows.extend(rec.item for rec in records if not rec.deleted)
        return rows

    def save_items(self, level: Any, ident: Any, items: Iterable[Item],
                   owner: Optional[str] = None) -> Dict[str, Any]:
        with self.locked():
            return self.replace_all(level, ident, list(items), owner)

    def _stage(self, folder: Path,
               groups: List[List[Item]]) -> Dict[str, List[Record]]:
        staging = folder / STAGING
        if staging.exists():
            self._drop_tree(staging)
        staging.mkdir(mode=DIR_MODE)
        blocks: Dict[str, List[Record]] = {}
        for number, group in enumerate(groups, 1):
            name = block_name(number)
            blocks[name] = [Record(item) for item in group]
            self._write(staging / name, encode_block(blocks[name]))
        return blocks

    def _swap(self, folder: Path) -> None:
        retired = folder / RETIRED
        if retired.exists():
            self._drop_tree(retired)
        current = folder / DATA
        if current.exists():
            os.rename(current, retired)
        os.rename(folder / STAGING, current)
        if retired.exists():
            self._drop_tree(retired)

    def replace_all(self, level: Any, ident: Any, items: List[Item],
                    owner: Optional[str] = None) -> Dict[str, Any]:
        folder = self._guard(self._ensure(level, ident, owner))
        capacity = int(self.meta(level, ident)["block_kb"]) * 1024
        groups = self._pack(items, capacity)
        blocks = self._stage(folder, groups)
        self._swap(folder)
        self._write_index(folder, level, ident, blocks)
        self._write_sum(folder)
        self._touch(folder, level, ident)
        return {"blocks": len(blocks), "items": len(items),
                "bytes": sum(len(item.data) for item in items)}

    def remove(self, level: Any, ident: Any, ids: Iterable[str]) -> int:
        chosen = {check_item(part) for part in ids}
        return self._tomb(level, ident, chosen)

    def remove_items(self, level: Any, ident: Any, ids: Iterable[str]) -> int:
        with self.locked():
            return self.remove(level, ident, ids)

    def rewrite(self, level: Any, ident: Any,
                blocks: Dict[str, List[Record]]) -> None:
        folder = self._guard(self.folder(level, ident))
        for name, records in blocks.items():
            check_block(name)
            self._write(folder / DATA / name, encode_block(records))
        self._write_index(folder, level, ident, blocks)
        self._write_sum(folder)
        self._touch(folder, level, ident)

    def rebuild(self, level: Any, ident: Any) -> Dict[str, int]:
        folder = self._guard(self.folder(level, ident))
        blocks = self.read_blocks(level, ident)
        live = sum(1 for rows in blocks.values()
                   for rec in rows if not rec.deleted)
        self._write_index(folder, level, ident, blocks)
        self._write_sum(folder)
        return {"blocks": len(blocks), "items": live}

    def verify(self, level: Any, ident: Any,
               archived: bool = False) -> str:
        folder = self._guard(self.folder(level, ident, archived))
        target = folder / CHECKSUM
        if not target.is_file():
            return "missing"
        stored = self._read(target).decode("utf-8", "replace")
        return "ok" if stored == self._sum_text(folder) else "mismatch"

    def drop(self, level: Any, ident: Any,
             archived: bool = False) -> Tuple[int, int]:
        folder = self._guard(self.folder(level, ident, archived))
        need(folder.is_dir(), f"记忆仓库不存在: {ident}", BadStore)
        removed, freed = 0, 0
        for name in (INDEX, CHECKSUM, META):
            path = folder / name
            if path.is_file() or path.is_symlink():
                freed += path.lstat().st_size
                path.unlink()
                removed += 1
        data = folder / DATA
        if data.is_symlink():
            raise BadStore(f"拒绝符号链接: {data}")
        if data.is_dir():
            for path in data.iterdir():
                freed += path.lstat().st_size
                path.unlink()
                removed += 1
            data.rmdir()
        folder.rmdir()
        return removed, freed

    def mark_defrag(self, level: Any, ident: Any, stamp: int) -> None:
        folder = self._guard(self.folder(level, ident))
        doc = self._read_json(folder / META, "元数据")
        doc["last_defrag_at"] = iso(stamp)
        self._write_json(folder / META, doc)

    def _mark(self, rows: List[Record], ids: set) -> tuple:
        shape = []
        found = 0
        for rec in rows:
            if not rec.deleted and rec.item.id in ids:
                found += 1
                shape.append(Record(rec.item, True))
            else:
                shape.append(rec)
        return shape, found

    def _tomb(self, level: Any, ident: Any, ids: set) -> int:
        folder = self._guard(self.folder(level, ident))
        blocks = self.read_blocks(level, ident)
        live = {rec.item.id for rows in blocks.values()
                for rec in rows if not rec.deleted}
        missing = sorted(ids - live)
        need(not missing, f"记忆项不存在: {','.join(missing)}", BadState)
        found = 0
        for name, rows in blocks.items():
            shape, hits = self._mark(rows, ids)
            found += hits
            self._write(folder / DATA / name, encode_block(shape))
        self._write_index(folder, level, ident, blocks)
        self._write_sum(folder)
        self._touch(folder, level, ident)
        return found

    def _ensure(self, level: Any, ident: Any,
                owner: Optional[str] = None) -> Path:
        folder = self.folder(level, ident)
        stamp = iso(now_ns())
        if folder.is_symlink():
            raise BadStore(f"拒绝符号链接: {folder}")
        folder.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        data = folder / DATA
        if data.is_symlink():
            raise BadStore(f"拒绝符号链接: {data}")
        data.mkdir(exist_ok=True, mode=DIR_MODE)
        meta = folder / META
        if meta.is_file():
            return folder
        need(not meta.is_symlink(), f"拒绝符号链接: {meta}", BadStore)
        record = {
            "schema": SCHEMA,
            "agent": check_id(ident),
            "level": parse_level(level).value,
            "owner": owner or os.environ.get("AIRY_MEMORY_OWNER") or "airy",
            "block_kb": self.block_kb,
            "created_at": stamp,
            "updated_at": stamp,
            "last_defrag_at": None,
        }
        self._write_json(meta, record)
        self._write_index(folder, level, ident, {})
        self._write_sum(folder)
        return folder

    def _pack(self, items: List[Item], capacity: int) -> List[List[Item]]:
        groups: List[List[Item]] = []
        current: List[Item] = []
        used = 0
        for item in items:
            size = len(encode_rec(Record(item)))
            if current and used + size > capacity:
                groups.append(current)
                current, used = [], 0
            current.append(item)
            used += size
        if current:
            groups.append(current)
        return groups

    def _placements(self, blocks: Dict[str, List[Record]]) -> Dict[str, dict]:
        place: Dict[str, dict] = {}
        for name, rows in blocks.items():
            offset = BLOCK_HEAD
            for rec in rows:
                size = len(encode_rec(rec))
                if not rec.deleted:
                    place[rec.item.id] = {"block": name,
                                          "offset": offset, "size": size}
                offset += size
        return place

    def _write_index(self, folder: Path, level: Any, ident: Any,
                     blocks: Dict[str, List[Record]]) -> None:
        doc = {
            "schema": SCHEMA,
            "agent": check_id(ident),
            "level": parse_level(level).value,
            "blocks": list(blocks),
            "items": self._placements(blocks),
            "generated_at": iso(now_ns()),
        }
        self._write_json(folder / INDEX, doc)

    def _sum_text(self, folder: Path) -> str:
        rows = []
        data = folder / DATA
        if data.is_dir() and not data.is_symlink():
            for path in sorted(data.iterdir(), key=lambda item: item.name):
                if not path.name.endswith(".airyblk"):
                    continue
                digest = hashlib.sha256(self._read(path)).hexdigest()
                rows.append(f"{digest}  {DATA}/{path.name}")
        return "".join(f"{row}\n" for row in rows)

    def _write_sum(self, folder: Path) -> None:
        atomic_write(folder / CHECKSUM, self._sum_text(folder))

    def _touch(self, folder: Path, level: Any, ident: Any) -> None:
        doc = self._read_json(folder / META, "元数据")
        doc["updated_at"] = iso(now_ns())
        self._write_json(folder / META, doc)

    def _guard(self, path: Path) -> Path:
        probe = self.root
        try:
            parts = path.relative_to(self.root).parts
        except ValueError as exc:
            raise BadStore(f"路径越出记忆根: {path}") from exc
        for part in parts:
            probe = probe / part
            need(not probe.is_symlink(), f"拒绝符号链接: {probe}", BadStore)
        return path

    def _open(self, path: Path, flags: int, mode: int = FILE_MODE) -> int:
        need(not path.is_symlink(), f"拒绝符号链接: {path}", BadStore)
        try:
            return os.open(path, flags | os.O_NOFOLLOW, mode)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise BadStore(f"拒绝符号链接: {path}") from exc
            if exc.errno in (errno.EACCES, errno.EPERM):
                raise BadPerm(f"无权访问: {path}") from exc
            raise

    def _read(self, path: Path) -> bytes:
        fd = self._open(path, os.O_RDONLY)
        with os.fdopen(fd, "rb") as handle:
            return handle.read()

    def _read_json(self, path: Path, kind: str) -> Dict[str, Any]:
        need(path.is_file(), f"{kind}缺失: {path.name}", BadStore)
        try:
            doc = json.loads(self._read(path).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise BadStore(f"{kind}不是合法 JSON: {path.name}") from exc
        need(isinstance(doc, dict), f"{kind}必须是对象: {path.name}", BadStore)
        return doc

    def _write(self, path: Path, blob: bytes) -> None:
        fd = self._open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        with os.fdopen(fd, "wb") as handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())

    def _write_json(self, path: Path, doc: Dict[str, Any]) -> None:
        body = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True)
        atomic_write(path, body + "\n")

    def _drop_tree(self, path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink()
            return
        if not path.is_dir():
            return
        shutil.rmtree(path)
