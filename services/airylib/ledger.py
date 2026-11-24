# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 台账落盘原语
#
# 告警与事件两套台账共用同一份「紧凑 JSON + 原子替换」约定。序列化口径只
# 定义一次，读者不会因为两个模块各写一份 json.dumps 而拿到不同格式；写入
# 一律先写同目录临时文件、fsync 文件与目录再 rename，掉电后不会看到半份
# 记录，两个写者同时落盘也不会互相覆盖。

"""Atomic JSON persistence shared by the ledgers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["FILE_MODE", "atomic_write", "compact"]

FILE_MODE = 0o640


def compact(obj: Any) -> str:
    """单行 JSON：无多余空白，便于按行追加与逐行比对。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


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
