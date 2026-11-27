# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 台账落盘原语
#
# 告警与事件两套台账共用同一份「紧凑 JSON + 原子替换」约定。序列化口径只
# 定义一次，读者不会因为两个模块各写一份 json.dumps 而拿到不同格式；写入
# 一律先写同目录临时文件、fsync 文件与目录再 rename，掉电后不会看到半份
# 记录，两个写者同时落盘也不会互相覆盖。
#
# 追加型台账（擦除审计、工作站审计）不能整份替换，另给 append_line 与
# read_rows：O_APPEND 落一行并 fsync，读回时逐行解析、坏行跳过。

"""Atomic JSON persistence shared by the ledgers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["FILE_MODE", "append_line", "atomic_write", "compact",
           "read_rows"]

FILE_MODE = 0o640


def compact(obj: Any) -> str:
    """单行 JSON：无多余空白，便于按行追加与逐行比对。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def append_line(path: str | Path, obj: Any,
                mode: int = FILE_MODE) -> Path:
    """追加一行 JSON：O_APPEND + fsync，并发写不会互相覆盖半行。"""
    target = Path(path)
    line = json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, mode)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return target


def read_rows(path: str | Path, limit: int = 50) -> list[Any]:
    """逐行读回 JSON 对象，坏行跳过；limit<=0 表示不限条数。"""
    target = Path(path)
    if not target.is_file():
        return []
    rows = []
    for line in target.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows[-limit:] if limit > 0 else rows


def atomic_write(path: str | Path, text: str, mode: int = FILE_MODE) -> None:
    """整份替换 path：临时文件 + fsync + rename + fsync 目录。"""
    target = Path(path)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        os.unlink(tmp)
        raise
    _sync_dir(target.parent)


def _sync_dir(path: Path) -> None:
    """rename 之后同步目录项，确保掉电后新的名字已经可见。"""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
