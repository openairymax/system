# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 升级检查状态
#
# 状态文件只由 check 写：先写同目录临时文件再 rename，读者不会看到
# 半份 JSON。失败不覆盖 last_ok——一次 DNS 故障不该让系统「忘记」
# 已知的新版本；提醒去重记在 notified，同一 version/build 只提醒一次。

"""AirymaxOS update check state store (V-14-04).

设计依据：docs-linux/190-distribution/04-update-mechanism.md §2.4。
本模块只管状态的读写与原子替换，不取网络、不比较版本；结论由调用方
算好再存入，存储层不替调用方解释「什么算成功」。

"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from .model import STATE_DIR, STATE_FILE, SCHEMA_VERSION

__all__ = [
    "Corrupt",
    "Failure",
    "Finding",
    "State",
    "load",
    "record_error",
    "record_ok",
    "record_sent",
    "save",
    "state_path",
]

_KEYS = frozenset({"schema_version", "last_ok", "last_error", "notified"})

_OK_KEYS = frozenset({"version", "build", "checked_at"})

_ERR_KEYS = frozenset({"reason", "failed_at"})


class Corrupt(ValueError):
    """状态文件存在但不可解析。"""


@dataclass(frozen=True)
class Finding:
    """最近一次成功的检查结论。

    Attributes:
        version: 通道给出的版本号。
        build: 通道给出的构建号。
        checked_at: 本次检查的 UTC 时间戳。
    """

    version: str
    build: int
    checked_at: str


@dataclass(frozen=True)
class Failure:
    """最近一次失败的检查结论。

    Attributes:
        reason: 单行失败原因，不含换行。
        failed_at: 本次检查的 UTC 时间戳。
    """

    reason: str
    failed_at: str


@dataclass(frozen=True)
class State:
    """状态文件的完整内容。

    Attributes:
        last_ok: 最近一次成功结论，失败不清除。
        last_error: 最近一次失败原因，成功不清除。
        notified: 已提醒过的 ``version/build``。
    """

    last_ok: Finding | None = None
    last_error: Failure | None = None
    notified: str | None = None


def state_path(root: Path = Path(STATE_DIR)) -> Path:
    """返回状态文件路径。

    Args:
        root: 状态目录，缺省取模块常量。

    Returns:
        目录下的状态文件名。
    """
    return root / STATE_FILE


def _finding(value: object) -> Finding | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _OK_KEYS:
        raise Corrupt("last_ok: malformed")
    version = value["version"]
    build = value["build"]
    stamp = value["checked_at"]
    if not isinstance(version, str) or not isinstance(stamp, str):
        raise Corrupt("last_ok: malformed")
    if isinstance(build, bool) or not isinstance(build, int):
        raise Corrupt("last_ok.build: malformed")
    return Finding(version, build, stamp)


def _failure(value: object) -> Failure | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _ERR_KEYS:
        raise Corrupt("last_error: malformed")
    reason = value["reason"]
    stamp = value["failed_at"]
    if not isinstance(reason, str) or not isinstance(stamp, str):
        raise Corrupt("last_error: malformed")
    return Failure(reason, stamp)


def _decode(raw: str) -> State:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Corrupt(f"not JSON: {exc.msg}") from exc
    if not isinstance(data, dict) or set(data) != _KEYS:
        raise Corrupt("state: unexpected shape")
    if data["schema_version"] != SCHEMA_VERSION:
        raise Corrupt("state.schema_version: unsupported")
    ok = _finding(data["last_ok"])
    err = _failure(data["last_error"])
    notified = data["notified"]
    if notified is not None and not isinstance(notified, str):
        raise Corrupt("notified: malformed")
    return State(ok, err, notified)


def _encode(state: State) -> str:
    data = {
        "schema_version": SCHEMA_VERSION,
        "last_ok": None,
        "last_error": None,
        "notified": state.notified,
    }
    if state.last_ok is not None:
        item = state.last_ok
        data["last_ok"] = {
            "version": item.version,
            "build": item.build,
            "checked_at": item.checked_at,
        }
    if state.last_error is not None:
        item = state.last_error
        data["last_error"] = {
            "reason": item.reason,
            "failed_at": item.failed_at,
        }
    return json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n"


def load(path: Path = Path(STATE_DIR, STATE_FILE)) -> State:
    """读取状态文件。

    Args:
        path: 状态文件路径。

    Returns:
        解析后的状态；文件不存在时返回空状态。

    Raises:
        Corrupt: 文件存在但字段缺失、类型不符或 schema 不支持。
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return State()
    return _decode(raw)


def _sync(parent: Path) -> None:
    try:
        handle = os.open(parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def save(path: Path, state: State) -> None:
    """原子写入状态文件。

    Args:
        path: 状态文件路径。
        state: 待写入的完整状态。
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    text = _encode(state)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    _sync(parent)


def record_ok(state: State, finding: Finding) -> State:
    """记录一次成功结论，保留既有失败原因与提醒记录。"""
    return replace(state, last_ok=finding)


def record_error(state: State, failure: Failure) -> State:
    """记录一次失败原因，保留既有成功结论。"""
    return replace(state, last_error=failure)


def record_sent(state: State, notified: str) -> State:
    """记录已提醒的 ``version/build``。"""
    return replace(state, notified=notified)
