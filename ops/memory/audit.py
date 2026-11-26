# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 擦除审计
#
# 职责：把每一次擦除追加写进 /var/log/airy/memory_erase.log，一行一条 JSON，
# 永久保留；记录操作者、时间、范围、级别与遍数，供 erase audit 复核。
#
# 边界：只追加，不轮转、不改写、不删除历史——审计链的完整性优先于日志体积；
# 不做远程转发（属集中日志系统），也不校验操作者身份（取证以调用环境为准）。
#
# 依赖：标准库 json/os/pathlib，日志目录可由 AIRY_MEMORY_LOGDIR 覆盖。

"""AirymaxOS erase audit trail."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from airylib.err import need

from .model import BadStore

__all__ = ["append", "logdir", "read"]

DEFAULT_DIR = "/var/log/airy"
FILE = "memory_erase.log"


def logdir() -> Path:
    return Path(os.environ.get("AIRY_MEMORY_LOGDIR") or DEFAULT_DIR)


def append(doc: Dict[str, Any]) -> Path:
    need(isinstance(doc, dict), "审计记录必须是对象", BadStore)
    folder = logdir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o750)
    path = folder / FILE
    line = json.dumps(doc, ensure_ascii=False, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def read(limit: int = 50) -> List[Dict[str, Any]]:
    path = logdir() / FILE
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    if limit > 0:
        return rows[-limit:]
    return rows
