# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS Agent 日志读取与归档
#
# 日志根目录默认 /var/log/airy/agents/<id>/，读取 *.log 与 *.log.gz；
# 归档把超过保留期的文件压缩后移到 /var/lib/agentrt/log.archive/<id>/。
# 查询与归档都要求调用方属于 airy-log 组（设计 §11.3），不满足抛 BadPerm。
# 环境变量 AIRY_AGENT_GROUPS 可注入组名，便于测试；生产环境由 grp 解析。

"""Agent log tail, follow and retention archive."""

from __future__ import annotations

import gzip
import grp
import json
import os
import shutil
import time
from pathlib import Path

from airylib.err import need

from .model import NS, BadInput, BadPerm

LOG_ROOT = "/var/log/airy/agents"
LOG_ARCHIVE = "/var/lib/agentrt/log.archive"
GROUP = "airy-log"
CHUNK = 256 * 1024


def group_names() -> set[str]:
    raw = os.environ.get("AIRY_AGENT_GROUPS")
    if raw is not None:
        return {item.strip() for item in raw.split(",") if item.strip()}
    names: set[str] = set()
    for gid in {os.getgid(), *os.getgroups()}:
        try:
            names.add(grp.getgrgid(gid).gr_name)
        except KeyError:
            continue
    return names


def ensure_access() -> None:
    want = os.environ.get("AIRY_AGENT_LOG_GROUP") or GROUP
    need(want in group_names(), f"日志查询需要 {want} 组权限", BadPerm)


def log_files(root: str | Path, ident: str) -> list[Path]:
    folder = Path(root) / ident
    if not folder.is_dir():
        return []
    files = [*folder.glob("*.log"), *folder.glob("*.log.gz")]
    return sorted(files, key=lambda item: item.name)


def _tail_text(path: Path, limit: int = CHUNK) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            return fh.read()[-limit:]
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > limit:
            fh.seek(size - limit)
        return fh.read().decode("utf-8", errors="replace")


def tail_lines(root: str | Path, ident: str, count: int) -> list[str]:
    need(isinstance(count, int) and count > 0, "--lines 必须是正整数",
         BadInput)
    rows: list[str] = []
    for path in reversed(log_files(root, ident)):
        rows = _tail_text(path).splitlines() + rows
        if len(rows) >= count:
            break
    return rows[-count:]


def follow_logs(root: str | Path, ident: str, count: int,
                out=print) -> None:
    for line in tail_lines(root, ident, count):
        out(line)
    seen = {path: path.stat().st_size for path in log_files(root, ident)}
    pending: dict[Path, str] = {}
    while True:
        for path in log_files(root, ident):
            offset = seen.get(path, 0)
            size = path.stat().st_size
            if size <= offset:
                continue
            start = 0 if size < offset else offset
            with path.open("rb") as fh:
                fh.seek(start)
                chunk = fh.read()
            seen[path] = start + len(chunk)
            text = pending.get(path, "") + chunk.decode("utf-8", "replace")
            parts = text.split("\n")
            pending[path] = parts.pop()
            for line in parts:
                out(line)
        time.sleep(0.5)


def _is_error(line: str, low: int, high: int) -> bool:
    try:
        doc = json.loads(line)
    except ValueError:
        return False
    if not isinstance(doc, dict):
        return False
    if str(doc.get("level", "")).upper() not in ("ERROR", "FATAL"):
        return False
    stamp = doc.get("ts_ns")
    return isinstance(stamp, int) and low <= stamp <= high


def recent_errors(root: str | Path, ident: str, stamp: int,
                  window: int = 60) -> int | None:
    files = log_files(root, ident)
    if not files:
        return None
    low, high = stamp - window * NS, stamp
    total = 0
    for path in files:
        total += sum(1 for line in _tail_text(path).splitlines()
                     if _is_error(line, low, high))
    return total


def archive_logs(root: str | Path, archive: str | Path, ident: str,
                 days: int, stamp: int) -> list[str]:
    need(isinstance(days, int) and days >= 0, "日志保留天数必须是非负整数",
         BadInput)
    cutoff = stamp / NS - days * 86400
    target_dir = Path(archive) / ident
    moved: list[str] = []
    for path in log_files(root, ident):
        if path.stat().st_mtime >= cutoff:
            continue
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
        name = path.name if path.name.endswith(".gz") else f"{path.name}.gz"
        target = target_dir / name
        if path.name.endswith(".gz"):
            shutil.copyfile(path, target)
        else:
            with path.open("rb") as src, gzip.open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
        path.unlink()
        moved.append(name)
    return moved
