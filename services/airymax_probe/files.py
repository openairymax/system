# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 探针用到的内核文本节点读写
#
# tracefs、securityfs 与 procfs 的节点形状只有两类：只读单值、只写控制
# 请求。读取失败必须折成 None 并保留失败原因，不能把「节点不存在」和
# 「值为零」混为一谈——后者会让探针把「通道未提供」误报成「计数为零」。
# 写入失败同样要留下 errno 名称，它是「注入不受理」的唯一可复算证据。
#
# 设计依据：docs-linux/90-observability/04-sysfs-procfs.md 与
# 0.0.4 方案 §6.7 X-02。

"""AirymaxOS probe file helpers."""

from __future__ import annotations

import errno
import os
from pathlib import Path

__all__ = ["clear", "mode", "num", "read", "text", "write"]

MAX_READ = 16 * 1024 * 1024


def text(path: Path) -> str | None:
    """读取一个节点；缺失、为空或不可读均返回 None。"""
    try:
        data = path.read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    data = data.strip()
    return data or None


def num(path: Path) -> int | None:
    """读取一个整数节点；缺失或非整数返回 None。"""
    data = text(path)
    if data is None:
        return None
    try:
        return int(data.split()[0], 10)
    except (IndexError, ValueError):
        return None


def mode(path: Path) -> str | None:
    """返回节点的八进制权限位，用于区分只读通道与可写通道。"""
    try:
        return f"{path.stat().st_mode & 0o777:04o}"
    except OSError:
        return None


def write(path: Path, value: str) -> str | None:
    """向节点写入文本；不创建、不截断，失败返回小写 errno 名称。"""
    try:
        handle = os.open(path, os.O_WRONLY | os.O_CLOEXEC)
    except OSError as exc:
        return _errno(exc)
    try:
        os.write(handle, value.encode("ascii"))
    except OSError as exc:
        return _errno(exc)
    finally:
        os.close(handle)
    return None


def clear(path: Path) -> str | None:
    """以截断方式打开节点；ftrace 的 trace 文件靠 O_TRUNC 清空缓冲。"""
    try:
        handle = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_CLOEXEC)
    except OSError as exc:
        return _errno(exc)
    os.close(handle)
    return None


def read(path: Path) -> str | None:
    """读取有上限的节点全文，供 trace 这类可能很大的文件使用。"""
    try:
        with open(path, "r", encoding="ascii", errors="replace") as handle:
            return handle.read(MAX_READ)
    except OSError:
        return None


def _errno(exc: OSError) -> str:
    return errno.errorcode.get(exc.errno or 0, "eio").lower()