# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# airy-memory 参数定义
#
# 职责：把设计 §9 的命令面对齐成 argparse 子命令，并把解析结果交给 cli.py
# 的实现；参数只做形态约束，语义校验（层级、ID、方法）放在 model 层。
#
# 边界：不访问文件系统、不读配置、不打印——保证 --help 可在任何环境执行。
#
# 依赖：标准库 argparse。

"""airy-memory command line parser."""

from __future__ import annotations

import argparse
from typing import Any, Callable, Dict

__all__ = ["build"]

Handler = Callable[[Any, argparse.Namespace], int]


def _level(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--level", default="L3",
                        help="记忆层级 L3 或 L4（clone 可用逗号分隔多个）")


def _json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true",
                        help="输出机器可读 JSON")


def _node(subs: Any, name: str, handlers: Dict[str, Handler]) -> Any:
    parser = subs.add_parser(name)
    parser.set_defaults(handler=handlers[name])
    return parser


def build(handlers: Dict[str, Handler]) -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="airy-memory", description="AirymaxOS 记忆运维（L3/L4）")
    root.add_argument("--root", default=None,
                      help="记忆仓库根（默认 /var/lib/airy/memory）")
    root.add_argument("--conf", default=None,
                      help="配置文件（默认 /etc/agentrt/memory.conf）")
    root.add_argument("--log-dir", default=None,
                      help="擦除审计目录（默认 /var/log/airy）")
    subs = root.add_subparsers(dest="command", required=True)

    status = _node(subs, "status", handlers)
    status.add_argument("agent_id")
    _json(status)

    stats = _node(subs, "stats", handlers)
    stats.add_argument("agent_id")
    _json(stats)

    listing = _node(subs, "list", handlers)
    listing.add_argument("agent_id")
    listing.add_argument("--level", default="L3")
    _json(listing)

    search = _node(subs, "search", handlers)
    search.add_argument("agent_id")
    search.add_argument("keyword")
    search.add_argument("--level", default="L3")
    _json(search)

    defrag = _node(subs, "defrag", handlers)
    defrag.add_argument("target", nargs="?",
                        help="Agent ID 或 status")
    defrag.add_argument("--all", action="store_true", dest="all_agents")
    _level(defrag)
    defrag.add_argument("--now", action="store_true",
                        help="跳过时间窗口（排障与测试）")
    _json(defrag)

    for name in ("transfer", "clone"):
        node = _node(subs, name, handlers)
        node.add_argument("--from-agent", required=True, dest="from_agent")
        node.add_argument("--to-agent", required=True, dest="to_agent")
        node.add_argument("--items", default=None)
        _level(node)
        node.add_argument("--to-level", default=None, dest="to_level")
        node.add_argument("--consent", action="store_true",
                          help="跨属主迁移的显式同意")
        _json(node)

    erase = _node(subs, "erase", handlers)
    erase.add_argument("target", nargs="?",
                       help="Agent ID 或 status / audit")
    erase.add_argument("--agent-id", default=None, dest="agent_id")
    erase.add_argument("--items", default=None)
    erase.add_argument("--all", action="store_true", dest="all_items")
    erase.add_argument("--level", default="L3")
    erase.add_argument("--method", default=None,
                       help="quick/overwrite/secure/crypto")
    erase.add_argument("--lines", type=int, default=50)
    _json(erase)
    return root
