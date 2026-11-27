# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 告警取样
#
# 事件响应要从告警体系接手 CRITICAL / WARNING，这里只读告警的活动
# 状态文件，把它折算成事件的草案：级别、分类、标题与关联编号。取样
# 不写告警目录，两个子系统之间只有这一条只读依赖。

"""Turn firing alerts into incident drafts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .model import (TITLE_MAX, BadStore, Category, Severity)

SCHEMA = 1
ACTIVE = "active.json"
FIRING = "FIRING"
LEVELS = {"CRITICAL": Severity.P0, "WARNING": Severity.P1}
CATEGORY = {
    "sec_d": Category.SECURITY,
    "net_d": Category.NETWORK,
    "mem_d": Category.RESOURCE,
    "vfs_d": Category.RESOURCE,
    "cogn_d": Category.RESOURCE,
}


def _active_doc(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise BadStore(f"{path}: {exc}") from exc


def _active_rows(path: Path, rows: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise BadStore(f"{path}: alerts 含非对象项")
        out.append(dict(row))
    return out


def read_active(root: str | Path) -> list[dict[str, Any]]:
    """读活动告警；没有状态文件视为当前无告警。"""
    path = Path(root) / ACTIVE
    doc = _active_doc(path)
    if doc is None:
        return []
    if not isinstance(doc, Mapping) or doc.get("schema") != SCHEMA:
        raise BadStore(f"{path}: 活动告警 schema 不支持")
    rows = doc.get("alerts")
    if not isinstance(rows, list):
        raise BadStore(f"{path}: alerts 必须是数组")
    return _active_rows(path, rows)


def _ident(row: Mapping[str, Any], path: str) -> str:
    body = row.get("alert_id")
    if not isinstance(body, str) or not body.strip():
        raise BadStore(f"{path}: 活动告警缺 alert_id")
    return body.strip()


def _head(row: Mapping[str, Any]) -> str:
    source = str(row.get("source") or "").strip()
    code = row.get("code")
    message = str(row.get("message") or "").strip()
    seed = f"{source}/{code}" if code is not None else source
    return f"{seed}: {message}" if message else seed


def _agents(row: Mapping[str, Any]) -> list[int]:
    agent = row.get("agent")
    if isinstance(agent, bool) or not isinstance(agent, int) or agent <= 0:
        return []
    return [agent]


def _draft_level(row: Mapping[str, Any]):
    if row.get("state") != FIRING:
        return None
    return LEVELS.get(str(row.get("severity") or "").upper())


def _draft_meta(row: Mapping[str, Any], level: Any,
                source: str) -> dict[str, Any]:
    title = _head(row).strip() or "未命名告警"
    return {
        "severity": level,
        "category": CATEGORY.get(source, Category.SYSTEM),
        "title": title[:TITLE_MAX],
        "source": source or "alert",
        "detail": str(row.get("message") or "").strip(),
    }


def draft(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """把一行活动告警折算成建单参数；不该建单的返回 None。"""
    level = _draft_level(row)
    if level is None:
        return None
    source = str(row.get("source") or "").strip()
    doc = _draft_meta(row, level, source)
    doc["alerts"] = [_ident(row, ACTIVE)]
    doc["agents"] = _agents(row)
    doc["daemons"] = [source] if source.endswith("_d") else []
    return doc
