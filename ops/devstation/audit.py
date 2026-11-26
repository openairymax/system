# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""工作站操作审计。

每一次放行与拒绝都追加一行 JSON 到 /var/log/airy/devstation_audit.log：
操作者、级别、动作、目标、结果与原因。只追加不改写，admin 动作因此
天然永久保留；目录可由 AIRY_STATION_LOGDIR 覆盖，写入用 O_APPEND 加
fsync，两个终端并发也不会互相覆盖半行。
"""

import getpass
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from .model import iso, now_ns

DEFAULT_DIR = "/var/log/airy"
FILE = "devstation_audit.log"
ENV = "AIRY_STATION_LOGDIR"
OPERATOR = "AIRY_STATION_OPERATOR"


def logdir() -> Path:
    """审计目录，可由环境变量覆盖。"""
    return Path(os.environ.get(ENV) or DEFAULT_DIR)


def operator() -> str:
    """操作者标识：显式环境变量优先，否则取当前登录名。"""
    name = os.environ.get(OPERATOR)
    if name:
        return name
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return "unknown"


def append(doc: Dict[str, Any]) -> Path:
    """原子追加一条审计记录。"""
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


def record(level: str, action: str, target: str, result: str,
           detail: str = "") -> Path:
    """组装并落盘一条操作记录。"""
    doc = {
        "ts": iso(now_ns()),
        "operator": operator(),
        "level": level,
        "action": action,
        "target": str(target),
        "result": result,
        "detail": detail,
    }
    return append(doc)


def read(limit: int = 50) -> List[Dict[str, Any]]:
    """读取最近的审计记录，坏行跳过。"""
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
