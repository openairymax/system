# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# L3 碎片整理
#
# 职责：按设计 §6.2 的阈值决定是否整理，冻结写入（沿用仓库同一把 flock），
# 把存活项紧凑重排到新块、重建索引与校验和、删除旧块，并把进度与结果写进
# 状态文件供 defrag status 读取。
#
# 边界：只整理不迁移（transfer.py）、不擦除历史字节（erase.py）；时间窗口
# 来自配置，--now 只用于受控的立即执行（测试与排障），不改变判定阈值。
#
# 依赖：store.py 的锁与块读写、stats.py 的事实统计、model.py 的时间工具。

"""AirymaxOS L3 defragmentation against the documented thresholds."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from airylib.err import need
from airylib.ledger import atomic_write

from . import stats
from .model import NS, BadState, BadStore, check_id, in_window, iso, now_ns, parse_iso, parse_level
from .store import Store

__all__ = ["run", "run_all", "state", "status"]

STATE = "state"
FILE = "defrag.json"
AGE_SECONDS = 7 * 86400


def _state_path(store: Store) -> Path:
    return store.root / STATE / FILE


def _save(store: Store, doc: Dict[str, Any]) -> None:
    folder = store.root / STATE
    folder.mkdir(parents=True, exist_ok=True, mode=0o750)
    atomic_write(_state_path(store),
                 json.dumps(doc, ensure_ascii=False, indent=2,
                            sort_keys=True) + "\n")


def state(store: Store) -> Dict[str, Any]:
    path = _state_path(store)
    if not path.is_file():
        return {"state": "none"}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadStore(f"整理状态文件损坏: {path}") from exc
    need(isinstance(doc, dict), "整理状态必须是对象", BadStore)
    return doc


def status(store: Store) -> Dict[str, Any]:
    return state(store)


def _frag_reason(before: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    if before["fragmentation_pct"] > float(cfg["defrag_threshold_pct"]):
        return "fragmentation"
    return ""


def _press_reason(before: Dict[str, Any]) -> str:
    if (before["blocks"] > 10000
            and before["utilization_pct"] < stats.LOW_UTIL):
        return "block-utilization"
    if before["index_ratio_pct"] > stats.INDEX_SHARE:
        return "index-ratio"
    return ""


def _age_reason(meta: Dict[str, Any], stamp: int) -> str:
    last = meta.get("last_defrag_at")
    if not last:
        return "age"
    try:
        moment = parse_iso(last)
    except Exception as exc:
        raise BadStore(f"last_defrag_at 不合法: {last!r}") from exc
    if (stamp - moment) / NS > AGE_SECONDS:
        return "age"
    return ""


def reason_for(before: Dict[str, Any], cfg: Dict[str, Any],
               meta: Dict[str, Any], stamp: int) -> str:
    return (_frag_reason(before, cfg) or _press_reason(before)
            or _age_reason(meta, stamp))


def _guard_window(cfg: Dict[str, Any], stamp: int, now: bool) -> None:
    if now:
        return
    window = str(cfg["defrag_window"])
    need(in_window(window, stamp),
         f"当前不在 defrag 时间窗口 {window}，确需立即整理请加 --now",
         BadState)


def _perform(store: Store, rank: Any, agent: Any,
             moment: int) -> List[Any]:
    with store.locked():
        items = store.items(rank, agent)
        store.replace_all(rank, agent, items)
        store.mark_defrag(rank, agent, moment)
    return items


def run(store: Store, cfg: Dict[str, Any], level: Any, ident: Any,
        stamp: Optional[int] = None, now: bool = False) -> Dict[str, Any]:
    moment = now_ns() if stamp is None else stamp
    rank = parse_level(level)
    agent = check_id(ident)
    need(store.exists(rank, agent), f"记忆仓库不存在: {agent}", BadStore)
    _guard_window(cfg, moment, now)
    meta = store.meta(rank, agent)
    before = stats.level_stats(store, rank, agent)
    trigger = reason_for(before, cfg, meta, moment)
    if not trigger:
        return {"state": "skipped", "reason": "threshold-not-met",
                "agent": agent, "level": rank.value, "before": before}
    started = time.monotonic()
    _save(store, {"state": "running", "progress_pct": 0, "agent": agent,
                  "level": rank.value, "trigger": trigger,
                  "started_at": iso(moment)})
    items = _perform(store, rank, agent, moment)
    after = stats.level_stats(store, rank, agent)
    elapsed = int((time.monotonic() - started) * 1000)
    doc = {
        "state": "done",
        "progress_pct": 100,
        "agent": agent,
        "level": rank.value,
        "trigger": trigger,
        "items": len(items),
        "before": before,
        "after": after,
        "freed_bytes": max(before["data_bytes"] - after["data_bytes"], 0),
        "duration_ms": elapsed,
        "started_at": iso(moment),
        "finished_at": iso(now_ns()),
    }
    _save(store, doc)
    return doc


def run_all(store: Store, cfg: Dict[str, Any], level: Any,
            stamp: Optional[int] = None,
            now: bool = False) -> List[Dict[str, Any]]:
    moment = now_ns() if stamp is None else stamp
    rank = parse_level(level)
    _guard_window(cfg, moment, now)
    return [run(store, cfg, rank, ident, moment, True)
            for ident in store.ids(rank)]
