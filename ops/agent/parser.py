# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS Agent 运维参数解析
#
# 子命令面与设计 §9 的 airyctl agent 子树逐项对应，单独成模块以保持
# cli.py 只负责「读存储、跑迁移、打印结果」。处理函数由调用方注入，
# 解析器不反向依赖命令行实现，避免循环导入。

"""Argument parser for the agent operations command line."""

from __future__ import annotations

import argparse
from typing import Any, Callable

DESCRIPTION = (
    "AirymaxOS Agent 运维命令行（对应设计文档 airyctl agent 子命令）")
EPILOG = (
    "log 子命令需要 airy-log 组；migrate/upgrade 本版单节点不提供")

Handler = Callable[[argparse.Namespace, Any], int]


def _common(parser: argparse.ArgumentParser, js: bool = False) -> None:
    if js:
        parser.add_argument("--json", action="store_true", help="输出 JSON")


def build(handlers: dict[str, Handler]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airy-agent", description=DESCRIPTION, epilog=EPILOG)
    parser.add_argument("--root", default=None,
                        help="Agent 元数据根目录（缺省取 AIRY_AGENT_ROOT）")
    parser.add_argument("--log-root", default=None, help="日志根目录")
    parser.add_argument("--log-archive", default=None, help="日志归档目录")
    parser.add_argument("--conf", default=None, help="配置文件路径")
    sub = parser.add_subparsers(dest="cmd", required=True)

    one = sub.add_parser("create", help="创建 Agent")
    one.add_argument("--name", required=True, help="Agent 名")
    one.add_argument("--template", required=True, help="模板名")
    one.add_argument("--token-budget", type=int, help="令牌总额")
    one.add_argument("--memory-l1", help="L1 内存，如 256MB")
    one.add_argument("--memory-l2", help="L2 内存，如 1GB")
    one.add_argument("--sched-runtime", help="运行时间片，如 10ms")
    one.add_argument("--sched-deadline", help="截止时间，如 30ms")
    one.add_argument("--sched-period", help="调度周期，如 100ms")
    one.add_argument("--capabilities", help="逗号分隔的能力名")
    _common(one, js=True)
    one.set_defaults(func=handlers["create"])

    for name, text in (
            ("start", "启动 Agent"),
            ("pause", "暂停调度"),
            ("resume", "恢复调度"),
            ("stop", "停止并保留状态"),
            ("terminate", "终止并归档元数据"),
            ("restart", "重启 Agent")):
        item = sub.add_parser(name, help=text)
        item.add_argument("id")
        _common(item, js=True)
        item.set_defaults(func=handlers[name])

    stat = sub.add_parser("status", help="查看 Agent 状态")
    stat.add_argument("id")
    _common(stat, js=True)
    stat.set_defaults(func=handlers["status"])

    listing = sub.add_parser("list", help="列出 Agent")
    listing.add_argument("--state", help="按 8 态过滤")
    _common(listing, js=True)
    listing.set_defaults(func=handlers["list"])

    tuned = sub.add_parser("tune", help="调整调度与内存")
    tuned.add_argument("id")
    tuned.add_argument("--sched-runtime", "--runtime", dest="sched_runtime")
    tuned.add_argument("--sched-deadline", "--deadline", dest="sched_deadline")
    tuned.add_argument("--sched-period", "--period", dest="sched_period")
    tuned.add_argument("--memory-l1")
    tuned.add_argument("--memory-l2")
    tuned.add_argument("--auto", action="store_true", help="恢复自动调优默认值")
    _common(tuned, js=True)
    tuned.set_defaults(func=handlers["tune"])

    token = sub.add_parser("token", help="令牌预算")
    token.add_argument("id")
    act = token.add_subparsers(dest="action", required=True)
    budget = act.add_parser("budget", help="设置总额")
    budget.add_argument("amount", type=int)
    _common(budget, js=True)
    budget.set_defaults(func=handlers["token"])
    reset = act.add_parser("reset", help="清零已用量")
    _common(reset, js=True)
    reset.set_defaults(func=handlers["token"])
    tstat = act.add_parser("status", help="查看用量")
    _common(tstat, js=True)
    tstat.set_defaults(func=handlers["token"])

    log = sub.add_parser("log", help="读取或归档日志")
    log.add_argument("items", nargs="+", metavar="ID|archive ID")
    log.add_argument("--lines", type=int, default=100)
    log.add_argument("--follow", action="store_true", help="持续跟踪")
    log.set_defaults(func=handlers["log"])

    hp = sub.add_parser("health", help="计算健康分（设计 §3）")
    hp.add_argument("id")
    _common(hp, js=True)
    hp.set_defaults(func=handlers["health"])

    mig = sub.add_parser("migrate", help="跨节点迁移（本版不提供）")
    mig.add_argument("items", nargs="*", metavar="ID")
    mig.add_argument("--target-node")
    mig.set_defaults(func=handlers["migrate"])

    upg = sub.add_parser("upgrade", help="滚动升级（本版不提供）")
    upg.add_argument("items", nargs="*", metavar="ARG")
    upg.add_argument("--group")
    upg.add_argument("--version")
    upg.add_argument("--rollback", action="store_true")
    upg.set_defaults(func=handlers["upgrade"])
    return parser
