# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""airy-station 参数定义。

全局选项在子命令前后都能写：子解析器上的副本使用 SUPPRESS 默认值，
不会把根解析器已取到的值覆盖回默认。远程与 Web 选项仍然解析，但由
命令行层显式拒绝，避免 argparse 静默忽略。
"""

import argparse

DESC = "AirymaxOS 开发运维工作站（TUI 与单次快照）"
EPILOG = ("远程节点、mTLS RPC 与 Web UI 属 v1.1.0；"
          "本版显式拒绝而不是假装连接")
ACTIONS = ("start", "pause", "resume", "stop", "restart")


def _flags(parser, suppress: bool = False) -> None:
    """注册全局选项；子命令副本用 SUPPRESS 避免覆盖。"""
    extra = {"default": argparse.SUPPRESS} if suppress else {}
    parser.add_argument("--root", default=extra.get("default"),
                        help="数据根（默认 /）")
    parser.add_argument("--conf", default=extra.get("default"),
                        help="配置文件（默认 /etc/agentrt/devstation.conf）")
    parser.add_argument("--log-dir", default=extra.get("default"),
                        dest="log_dir", help="审计目录（默认 /var/log/airy）")
    parser.add_argument("--level", default=extra.get("default"),
                        help="权限级别 view/operate/admin")
    parser.add_argument("--json", action="store_true",
                        default=extra.get("default", False),
                        help="输出机器可读 JSON")
    parser.add_argument("--once", action="store_true",
                        default=extra.get("default", False),
                        help="只渲染一帧后退出")
    parser.add_argument("--yes", action="store_true",
                        default=extra.get("default", False),
                        help="admin 动作的二次确认")


def _remote(parser, suppress: bool = False) -> None:
    """注册 v1.1.0 的远程与 Web 选项，命令行层负责显式拒绝。"""
    default = argparse.SUPPRESS if suppress else None
    parser.add_argument("--web", action="store_true",
                        default=argparse.SUPPRESS if suppress else False,
                        help="v1.1.0 的 Web UI")
    parser.add_argument("--node", default=default,
                        help="v1.1.0 的远程节点")
    parser.add_argument("--rpc-port", type=int, default=default,
                        dest="rpc_port", help="v1.1.0 的远程 RPC 端口")
    parser.add_argument("--port", type=int, default=default,
                        help="v1.1.0 的 Web 监听端口")


def _node(subs, name: str, text: str):
    parser = subs.add_parser(name, help=text)
    _flags(parser, suppress=True)
    _remote(parser, suppress=True)
    return parser


def build() -> argparse.ArgumentParser:
    """构造解析器；无子命令时进入交互界面。"""
    root = argparse.ArgumentParser(prog="airy-station", description=DESC,
                                   epilog=EPILOG)
    _flags(root)
    _remote(root)
    subs = root.add_subparsers(dest="command", required=False)

    _node(subs, "monitor", "守护进程监控")
    agents = _node(subs, "agents", "Agent 列表与操作")
    agents.add_argument("--id", default=None, help="Agent 编号")
    agents.add_argument("--action", choices=ACTIONS, default=None,
                        help="对指定 Agent 执行操作")
    logs = _node(subs, "logs", "日志视图")
    logs.add_argument("--lines", type=int, default=0, help="保留行数")
    alerts = _node(subs, "alerts", "告警视图")
    alerts.add_argument("--lines", type=int, default=0, help="保留条数")
    _node(subs, "incidents", "事件视图")
    memory = _node(subs, "memory", "记忆视图与擦除")
    memory.add_argument("--tier", default="L3", help="记忆层级 L3/L4")
    memory.add_argument("--agent", default=None, help="Agent 编号")
    memory.add_argument("--action", choices=("erase",), default=None,
                        help="擦除记忆（admin）")
    memory.add_argument("--items", default=None, help="逗号分隔的记忆项")
    memory.add_argument("--all", action="store_true", dest="all_items",
                        help="擦除该层级全部项")
    memory.add_argument("--method", default=None,
                        help="quick/overwrite/secure/crypto")
    _node(subs, "backup", "备份视图")
    _node(subs, "config", "配置视图")
    _node(subs, "snapshot", "完整快照")
    return root
