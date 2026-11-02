# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 模型运行状态持久化
#
# 状态文件是缓存，不是事实来源：它的内容全部由引擎每次启动时重新判定，
# 磁盘上的副本只用于让外部工具在不启动推理的前提下读到上一次结论。
# 因此读取失败一律当作「还没有状态」，由引擎重算并覆写；写入则必须
# 原子完成——半截文件既不是旧值也不是新值，外部工具会照它做误判。

"""AirymaxOS model runtime status persistence (K-20-05).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
（状态里必须留下 degraded:<原因>，不得静默降级）。

"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from . import model, paths

__all__ = ["encode", "load", "store"]

STATUS_FIELDS = frozenset({"schema", "state", "backend", "reason", "digest"})


def encode(entry: model.Status) -> dict:
    """把状态对象摊成落盘字段。

    Args:
        entry: 待落盘的状态。

    Returns:
        只含 :data:`STATUS_FIELDS` 的字典；无原因时 ``reason`` 为空串。
    """
    return {
        "schema": model.SCHEMA_VERSION,
        "state": entry.state.value,
        "backend": entry.backend.value,
        "reason": "" if entry.reason is None else entry.reason.value,
        "digest": entry.digest,
    }


def store(root: str | Path | None, entry: model.Status) -> Path:
    """原子写入状态文件并返回其位置。

    Args:
        root: 运行根；``None`` 时取契约根。
        entry: 待落盘的状态。

    Returns:
        状态文件路径。
    """
    home = paths.ensure(root)
    target = paths.status(home)
    text = json.dumps(encode(entry), sort_keys=True, indent=2) + "\n"
    _write(target, text)
    return target


def load(root: str | Path | None) -> model.Status | None:
    """读取上一次判定；缺失或不可信时返回 ``None``。

    Args:
        root: 运行根；``None`` 时取契约根。

    Returns:
        与写入时等价的状态；文件缺失、损坏或不自洽时为 ``None``。
    """
    try:
        text = paths.status(paths.base(root)).read_text(encoding="utf-8")
    except OSError:
        return None
    return _parse(text)


def _write(target: Path, text: str) -> None:
    handle, tmp = tempfile.mkstemp(dir=target.parent, prefix=".status-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, target)
    except BaseException:
        os.unlink(tmp)
        raise


def _parse(text: str) -> model.Status | None:
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, dict) or set(obj) != STATUS_FIELDS:
        return None
    if obj["schema"] != model.SCHEMA_VERSION:
        return None
    try:
        entry = model.Status(
            state=model.State(obj["state"]),
            backend=model.Backend(obj["backend"]),
            reason=_reason(obj["reason"]),
            digest=obj["digest"],
        )
    except (TypeError, ValueError):
        return None
    if not isinstance(entry.digest, str):
        return None
    return entry if _sound(entry) else None


def _reason(token) -> model.Reason | None:
    return None if token == "" else model.Reason(token)


def _sound(entry: model.Status) -> bool:
    if entry.state is model.State.READY:
        return entry.reason is None
    return entry.reason is not None
