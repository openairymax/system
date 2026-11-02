# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 实时带宽命令行入口
#
# 三个子命令对应一张契约表：status 只读判定当前预算是否落地，apply 写入
# 并逐 slice 回读，config 打印参考 sysctl 文本而不落盘。启动单元与验收
# 门禁共用同一份判定实现，避免「启动时写成功、门禁读失败」的两套口径。
#
# 退出码：0 表示已全部落地或内核不限预算（无需处理）；3 表示部分落地；
# 1 表示未通过且没有任何 slice 落地；2 为用法错误。门禁据此直接判读，
# 不必解析人类可读输出。

"""AirymaxOS RT bandwidth command line (X-01).

设计依据：0.0.4 方案 §6.7 X-01 与 docs-linux/100-operations/
02-configuration.md §2.2。``--json`` 输出与 :class:`Report` 的
``dump()`` 同形，二者是同一份数据的两种呈现。

"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from . import bandwidth, config
from .policy import (
    DEFAULT_CGROUP,
    DEFAULT_PERIOD,
    DEFAULT_TOTAL,
    MARK,
    Policy,
)
from .report import Report, State

__all__ = ["EXIT_FAIL", "EXIT_PARTIAL", "main"]

EXIT_FAIL = 1

EXIT_PARTIAL = 3

PASSING = (State.OK, State.DISABLED)


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行并分派；返回值即进程退出码。"""
    args = _args(argv)
    policy = _policy(args)
    if args.cmd == "config":
        sys.stdout.write(config.reference())
        return 0
    if args.cmd == "apply":
        found = bandwidth.apply(policy)
    else:
        found = bandwidth.inspect(policy)
    _emit(found, args.json)
    return _code(found.state)


def _opt() -> argparse.ArgumentParser:
    """每次新建一份公共选项，避免父解析器共享 action 对象。

    argparse 的 parents 只拷贝 action 引用，主解析器上的默认值会被
    子解析器继承，从而在选项位于子命令之前时把它重置。各自持有独立
    action 并把默认值设为 SUPPRESS，子命令才不会覆盖已解析的值。
    """
    opt = argparse.ArgumentParser(add_help=False)
    opt.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="以 JSON 输出",
    )
    opt.add_argument(
        "--proc",
        metavar="FILE",
        default=argparse.SUPPRESS,
        help="总预算接口路径",
    )
    opt.add_argument(
        "--period",
        metavar="FILE",
        default=argparse.SUPPRESS,
        help="周期接口路径",
    )
    opt.add_argument(
        "--cgroup",
        metavar="DIR",
        default=argparse.SUPPRESS,
        help="cgroup 挂载根",
    )
    return opt


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="airy-rt-cap",
        parents=[_opt()],
        description="AirymaxOS 实时带宽管理",
    )
    parser.set_defaults(
        json=False,
        proc=DEFAULT_TOTAL,
        period=DEFAULT_PERIOD,
        cgroup=DEFAULT_CGROUP,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser(
        "status", parents=[_opt()], help="只读判定当前预算"
    )
    sub.add_parser(
        "apply", parents=[_opt()], help="写入预算并回读"
    )
    sub.add_parser(
        "config", parents=[_opt()], help="打印参考 sysctl 文本"
    )
    return parser.parse_args(argv)


def _policy(args: argparse.Namespace) -> Policy:
    return Policy(
        total_path=args.proc,
        period_path=args.period,
        cgroup_root=args.cgroup,
    )


def _emit(found: Report, as_json: bool) -> None:
    if as_json:
        print(json.dumps(found.dump(), ensure_ascii=False))
        return
    print(
        f"{MARK} state={found.state.value}"
        f" applied={found.applied}/{found.wanted}"
    )
    print(
        f"total  {found.total} us  period {found.period} us"
        f"  share {found.share} us"
    )
    print(f"layout {found.kind.value if found.kind else '-'}")
    for item in found.landings:
        mark = "ok" if item.applied else "missing"
        print(f"  {item.slice:<22}{item.budget:>8}  {mark}")
    print(f"advice {config.advice(found.state)}")


def _code(state: State) -> int:
    if state in PASSING:
        return 0
    if state is State.PARTIAL:
        return EXIT_PARTIAL
    return EXIT_FAIL
