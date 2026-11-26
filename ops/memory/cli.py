# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# airy-memory 命令实现
#
# 职责：把 parser.py 解析出的参数落到 model/store/stats/defrag/transfer/erase/
# audit，负责文本与 --json 两种输出，并把可预期失败翻译成 2/3/4 退出码。
#
# 边界：实现保持薄层——判定、锁与落盘都在领域模块里，这里只做参数归一、
# 输出格式与错误映射；不连接 mem_d，也不读取任何运行时计数器。
#
# 依赖：本包各领域模块、airylib.err（need）、标准库 argparse/json/os/sys。

"""airy-memory command handlers."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List

from airylib.err import need

from . import config, defrag, erase, parser, stats, transfer
from .model import (
    BadInput,
    BadMemory,
    BadStore,
    Method,
    check_id,
    iso,
    parse_level,
)
from .store import Store

__all__ = ["main"]


@dataclass
class Ctx:
    store: Store
    cfg: Dict[str, Any]


def _dump(doc: Any) -> None:
    print(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True))


def _levels(text: Any) -> List[str]:
    parts = [part.strip() for part in str(text or "").split(",") if part.strip()]
    need(bool(parts), "至少需要一个记忆层级", BadInput)
    return parts


def _overview(ctx: Ctx, args: Any) -> tuple:
    agent = check_id(args.agent_id)
    report = stats.agent_status(ctx.store, agent)
    stats.require(report)
    return agent, report


def cmd_status(ctx: Ctx, args: Any) -> int:
    agent, report = _overview(ctx, args)
    if args.json:
        _dump(report)
        return 0
    print(f"agent={agent} archive_bytes={report['archive_bytes']}")
    for rank in ("L3", "L4"):
        row = report["levels"][rank]
        if not row["exists"]:
            print(f"{rank} exists=no")
            continue
        print(f"{rank} items={row['items']} blocks={row['blocks']} "
              f"bytes={row['data_bytes']} frag={row['fragmentation_pct']}% "
              f"util={row['utilization_pct']}% index={row['index_bytes']} "
              f"checksum={row['checksum']} owner={row['owner']}")
    return 0


def cmd_stats(ctx: Ctx, args: Any) -> int:
    _, report = _overview(ctx, args)
    if args.json:
        _dump(report)
        return 0
    for rank in ("L3", "L4"):
        row = report["levels"][rank]
        if not row["exists"]:
            print(f"{rank} exists=no")
            continue
        print(f"{rank} items={row['items']} live_bytes={row['live_bytes']} "
              f"dead_items={row['dead_items']} "
              f"dead_bytes={row['dead_bytes']} data_bytes={row['data_bytes']} "
              f"blocks={row['blocks']} block_kb={row['block_kb']} "
              f"index_bytes={row['index_bytes']} "
              f"index_ratio={row['index_ratio_pct']}% "
              f"frag={row['fragmentation_pct']}% "
              f"util={row['utilization_pct']}% checksum={row['checksum']}")
    print(f"archive_bytes={report['archive_bytes']}")
    return 0


def cmd_list(ctx: Ctx, args: Any) -> int:
    agent = check_id(args.agent_id)
    rank = parse_level(args.level)
    need(ctx.store.exists(rank, agent), f"记忆仓库不存在: {agent}", BadStore)
    items = ctx.store.items(rank, agent)
    if args.json:
        _dump({"agent": agent, "level": rank.value,
               "items": [item.to_json(rank.value) for item in items]})
        return 0
    for item in items:
        print(f"{item.id} {rank.value} bytes={len(item.data)} "
              f"sensitive={'yes' if item.sensitive else 'no'} "
              f"created={iso(item.created_at)}")
    return 0


def cmd_search(ctx: Ctx, args: Any) -> int:
    agent = check_id(args.agent_id)
    rank = parse_level(args.level)
    needle = str(args.keyword or "").strip()
    need(bool(needle), "搜索关键词不能为空", BadInput)
    need(ctx.store.exists(rank, agent), f"记忆仓库不存在: {agent}", BadStore)
    folded = needle.lower()
    hits = [item for item in ctx.store.items(rank, agent)
            if folded in item.id.lower() or folded in item.text().lower()]
    if args.json:
        _dump({"agent": agent, "level": rank.value, "keyword": needle,
               "items": [item.to_json(rank.value) for item in hits]})
        return 0
    for item in hits:
        print(f"{item.id} {rank.value} bytes={len(item.data)} "
              f"sensitive={'yes' if item.sensitive else 'no'}")
    return 0


def cmd_defrag(ctx: Ctx, args: Any) -> int:
    if args.target == "status":
        doc = defrag.status(ctx.store)
        if args.json:
            _dump(doc)
        elif doc.get("state") == "none":
            print("defrag state=none")
        else:
            print(f"defrag state={doc.get('state')} "
                  f"agent={doc.get('agent')} level={doc.get('level')} "
                  f"progress={doc.get('progress_pct')} "
                  f"freed_bytes={doc.get('freed_bytes', 0)} "
                  f"duration_ms={doc.get('duration_ms', 0)}")
        return 0
    need(bool(args.all_agents) ^ bool(args.target),
         "defrag 需要 Agent ID、status 或 --all", BadInput)
    rank = parse_level(args.level)
    if args.all_agents:
        docs = defrag.run_all(ctx.store, ctx.cfg, rank, now=args.now)
    else:
        docs = [defrag.run(ctx.store, ctx.cfg, rank, args.target,
                           now=args.now)]
    if args.json:
        _dump(docs if args.all_agents else docs[0])
        return 0
    for doc in docs:
        if doc["state"] == "skipped":
            print(f"defrag agent={doc['agent']} level={doc['level']} "
                  f"state=skipped reason={doc['reason']}")
            continue
        print(f"defrag agent={doc['agent']} level={doc['level']} "
              f"state={doc['state']} trigger={doc['trigger']} "
              f"items={doc['items']} "
              f"frag_before={doc['before']['fragmentation_pct']}% "
              f"frag_after={doc['after']['fragmentation_pct']}% "
              f"freed_bytes={doc['freed_bytes']} "
              f"duration_ms={doc['duration_ms']}")
    return 0


def _move(ctx: Ctx, args: Any, copy: bool) -> int:
    levels = _levels(args.level)
    need(not (len(levels) > 1 and args.to_level),
         "--to-level 与多层级列表同时使用有歧义", BadInput)
    docs = []
    for rank in levels:
        opts = {
            "from": args.from_agent,
            "to": args.to_agent,
            "items": args.items,
            "level": rank,
            "to_level": args.to_level,
            "consent": args.consent,
        }
        docs.append(transfer.clone(ctx.store, opts) if copy
                    else transfer.move(ctx.store, opts))
    if args.json:
        _dump(docs[0] if len(docs) == 1 else docs)
        return 0
    for doc in docs:
        print(f"{doc['mode']} from={doc['from_agent']} "
              f"to={doc['to_agent']} level={doc['from_level']} "
              f"items={doc['items']} bytes={doc['bytes']} "
              f"duration_ms={doc['duration_ms']}")
    return 0


def cmd_transfer(ctx: Ctx, args: Any) -> int:
    return _move(ctx, args, copy=False)


def cmd_clone(ctx: Ctx, args: Any) -> int:
    return _move(ctx, args, copy=True)


def _erase_row(row: Dict[str, Any]) -> str:
    return (f"erase agent={row.get('agent')} level={row.get('level')} "
            f"method={row.get('method')} passes={row.get('passes')} "
            f"scope={row.get('scope')} items={row.get('items')} "
            f"blocks={row.get('blocks')} bytes={row.get('bytes')} "
            f"operator={row.get('operator')} ts={row.get('ts')}")


def _erase_show(row: Dict[str, Any], args: Any) -> int:
    if args.json:
        _dump(row)
    else:
        print(_erase_row(row))
    return 0


def _erase_status(ctx: Ctx, args: Any) -> int:
    doc = erase.status(ctx.store)
    if doc.get("state") == "none" and not args.json:
        print("erase state=none")
        return 0
    return _erase_show(doc, args)


def _erase_audit(ctx: Ctx, args: Any) -> int:
    rows = erase.trail(args.lines)
    if args.json:
        _dump(rows)
        return 0
    for row in rows:
        print(_erase_row(row))
    return 0


def _erase_agent(args: Any, target: str) -> str:
    if args.agent_id:
        return str(args.agent_id)
    return target if target.isdigit() else ""


def _erase_norm(args: Any) -> tuple:
    level = args.level
    method = args.method
    named = {member.value for member in Method}
    if method is None and str(level).strip().lower() in named:
        method = level
        level = "L3"
    return level, method


def cmd_erase(ctx: Ctx, args: Any) -> int:
    target = str(args.target or "").strip()
    if target == "status":
        return _erase_status(ctx, args)
    if target == "audit":
        return _erase_audit(ctx, args)
    agent = _erase_agent(args, target)
    need(bool(agent), "erase 需要 Agent ID、status 或 audit", BadInput)
    need(not (args.items and args.all_items),
         "--items 与 --all 不能同时使用", BadInput)
    level, method = _erase_norm(args)
    opts = {"agent": agent, "items": args.items, "all": args.all_items,
            "level": level, "method": method}
    return _erase_show(erase.erase(ctx.store, ctx.cfg, opts), args)


HANDLERS = {
    "status": cmd_status,
    "stats": cmd_stats,
    "list": cmd_list,
    "search": cmd_search,
    "defrag": cmd_defrag,
    "transfer": cmd_transfer,
    "clone": cmd_clone,
    "erase": cmd_erase,
}


def main(argv: Any = None) -> int:
    args = parser.build(HANDLERS).parse_args(argv)
    try:
        cfg = config.load(args.conf)
        if args.log_dir:
            os.environ["AIRY_MEMORY_LOGDIR"] = str(args.log_dir)
        store = Store(args.root, int(cfg["l3_block_kb"]))
        return int(args.handler(Ctx(store, cfg), args) or 0)
    except BadMemory as exc:
        print(f"airy-memory: {exc}", file=sys.stderr)
        return int(getattr(exc, "code", 3))
    except OSError as exc:
        print(f"airy-memory: {exc}", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        print("airy-memory: 已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
