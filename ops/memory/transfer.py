# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 记忆项的迁移与克隆
#
# 职责：按设计 §7 在项级搬运记忆——同层、跨层、跨 Agent 都走同一条路径；
# 写入目标后同步重建目标索引与校验和，迁移还在源侧标记删除（碎片由 defrag
# 回收）。sensitive=true 的项一律拒绝，跨属主必须显式 --consent。
#
# 边界：不做 L1/L2 的导入导出（属数据面）；不做密钥搬迁或加密转码；不覆盖
# 目标同名项——冲突一律拒绝并保持源目标两侧原样。
#
# 依赖：store.py（锁、读写、墓碑）、model.py（层级与项校验）。

"""AirymaxOS memory item transfer and clone."""

from __future__ import annotations

import time
from typing import Any, Dict, Tuple

from airylib.err import need

from .model import BadInput, BadPerm, BadState, BadStore, Item, check_id, parse_items, parse_level
from .store import Store

__all__ = ["clone", "move"]


def _select(store: Store, level: Any, ident: Any,
            items: Any) -> Tuple[Dict[str, Any], Dict[str, Item]]:
    meta = store.meta(level, ident)
    live = {item.id: item for item in store.items(level, ident)}
    chosen = parse_items(items) if items else sorted(live)
    missing = [key for key in chosen if key not in live]
    need(not missing, f"记忆项不存在: {','.join(missing)}", BadState)
    return meta, {key: live[key] for key in chosen}


def _guard(store: Store, level: Any, ident: Any,
           opts: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Item]]:
    need(store.exists(level, ident), f"源记忆仓库不存在: {ident}", BadStore)
    meta, chosen = _select(store, level, ident, opts.get("items"))
    denied = sorted(key for key, item in chosen.items() if item.sensitive)
    need(not denied,
         f"敏感记忆项不可迁移或克隆: {','.join(denied)}", BadState)
    return meta, chosen


def _held(store: Store, level: Any, ident: Any, meta: Dict[str, Any],
          opts: Dict[str, Any]) -> Dict[str, Item]:
    if not store.exists(level, ident):
        return {}
    peer = store.meta(level, ident)
    need(meta.get("owner") == peer.get("owner") or bool(opts.get("consent")),
         "跨属主记忆迁移需要 --consent", BadPerm)
    return {item.id: item for item in store.items(level, ident)}


def _plan(store: Store, opts: Dict[str, Any], copy: bool) -> Dict[str, Any]:
    source = check_id(opts.get("from"))
    target = check_id(opts.get("to"))
    src_level = parse_level(opts.get("level") or "L3")
    dst_level = parse_level(opts.get("to_level") or src_level.value)
    need(source != target or src_level is not dst_level,
         "源与目标是同一容器，无需迁移", BadInput)
    started = time.monotonic()
    with store.locked():
        meta, chosen = _guard(store, src_level, source, opts)
        held = _held(store, dst_level, target, meta, opts)
        clash = [key for key in chosen if key in held]
        need(not clash, f"目标已存在同名记忆项: {','.join(clash)}", BadState)
        merged = list(held.values()) + list(chosen.values())
        store.replace_all(dst_level, target, merged,
                          owner=str(meta.get("owner") or ""))
        if not copy:
            store.remove(src_level, source, list(chosen))
    elapsed = int((time.monotonic() - started) * 1000)
    return {
        "state": "done",
        "mode": "clone" if copy else "move",
        "from_agent": source,
        "to_agent": target,
        "from_level": src_level.value,
        "to_level": dst_level.value,
        "items": len(chosen),
        "ids": list(chosen),
        "bytes": sum(len(item.data) for item in chosen.values()),
        "duration_ms": elapsed,
    }


def move(store: Store, opts: Dict[str, Any]) -> Dict[str, Any]:
    return _plan(store, opts, copy=False)


def clone(store: Store, opts: Dict[str, Any]) -> Dict[str, Any]:
    return _plan(store, opts, copy=True)
