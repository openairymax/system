# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 追踪取证命令行入口
#
# 三个子命令对应一张契约表：status 只读清点十条追踪通道、两条注入通道与
# 三处静态计数；sample 打开一个窗口采一轮事件；verify 采多轮并试探写入，
# 用来回答「同一动作重复 N 次计数是否单调递增」。三条路径共用同一份判定
# 实现，--json 与人类可读输出是同一份数据的两种呈现。
#
# 退出码：0 表示取证面完整可用；3 表示部分可用（缺哪条见 missing/samples）；
# 1 表示本机没有 airy 取证面；2 为用法错误。门禁据此直接判读。

"""AirymaxOS probe command line (X-02).

设计依据：0.0.4 方案 §6.7 X-02、§2.3 注入通道顺延登记，以及
docs-linux/90-observability/08-agent-tracing.md §2.2 的事件矩阵。

"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from . import probe
from .channels import Channel, Outcome
from .events import Event
from .paths import Paths, defaults
from .report import SAMPLE, VERIFY, Report, State

__all__ = ["EXIT_FAIL", "EXIT_PARTIAL", "main"]

PROG = "airy-probe"

EXIT_FAIL = 1

EXIT_PARTIAL = 3

MAX_WINDOW = 60.0

MAX_ROUNDS = 10


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行并分派；返回值即进程退出码。"""
    args = _args(argv)
    found = _run(args)
    _emit(found, args.json)
    return _code(found.state)


def _run(args: argparse.Namespace) -> Report:
    paths = _paths(args)
    if args.cmd == SAMPLE:
        return probe.sample(paths, args.window)
    if args.cmd == VERIFY:
        return probe.verify(paths, args.window, args.repeat)
    return probe.status(paths)


def _paths(args: argparse.Namespace) -> Paths:
    base = defaults()
    return Paths(
        tracefs=args.tracefs or base.tracefs,
        policy=args.policy or base.policy,
        agentrt=args.agentrt or base.agentrt,
        cap=args.cap or base.cap,
        stc=args.stc or base.stc,
    )


def _opt() -> argparse.ArgumentParser:
    """每次新建一份公共选项，避免父子解析器共享 action 的默认值。"""
    opt = argparse.ArgumentParser(add_help=False)
    opt.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS,
        help="以 JSON 输出",
    )
    opt.add_argument(
        "--tracefs", metavar="DIR", default=argparse.SUPPRESS,
        help="tracefs 挂载点",
    )
    opt.add_argument(
        "--policy", metavar="FILE", default=argparse.SUPPRESS,
        help="policy 表路径",
    )
    opt.add_argument(
        "--agentrt", metavar="DIR", default=argparse.SUPPRESS,
        help="agentrt 就绪节点目录",
    )
    opt.add_argument(
        "--cap", metavar="FILE", default=argparse.SUPPRESS,
        help="cap_used 计数路径",
    )
    opt.add_argument(
        "--stc", metavar="FILE", default=argparse.SUPPRESS,
        help="stc 统计路径",
    )
    opt.add_argument(
        "--window", metavar="SEC", type=_window, default=argparse.SUPPRESS,
        help=f"采样窗口，取值 (0, {MAX_WINDOW:g}]",
    )
    opt.add_argument(
        "--repeat", metavar="N", type=_repeat, default=argparse.SUPPRESS,
        help=f"verify 采样轮数，取值 [2, {MAX_ROUNDS}]",
    )
    return opt


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=PROG,
        parents=[_opt()],
        description="AirymaxOS 追踪取证与注入通道检查",
    )
    parser.set_defaults(
        json=False,
        tracefs=None,
        policy=None,
        agentrt=None,
        cap=None,
        stc=None,
        window=1.0,
        repeat=3,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", parents=[_opt()], help="只读清点取证面")
    sub.add_parser("sample", parents=[_opt()], help="采样一个追踪窗口")
    sub.add_parser("verify", parents=[_opt()], help="多轮采样并试探注入")
    return parser.parse_args(argv)


def _window(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("窗口必须是秒数") from None
    if not 0 < value <= MAX_WINDOW:
        raise argparse.ArgumentTypeError(f"窗口须在 (0, {MAX_WINDOW:g}] 内")
    return value


def _repeat(text: str) -> int:
    try:
        value = int(text, 10)
    except ValueError:
        raise argparse.ArgumentTypeError("轮数必须是整数") from None
    if not 2 <= value <= MAX_ROUNDS:
        raise argparse.ArgumentTypeError(f"轮数须在 [2, {MAX_ROUNDS}] 内")
    return value


def _emit(found: Report, as_json: bool) -> None:
    """两种呈现读同一份 Report：JSON 一种，分节的人类可读一种。"""
    if as_json:
        print(json.dumps(found.dump(), ensure_ascii=False))
        return
    print(
        f"{PROG} state={found.state.value} mode={found.mode}"
        f" readable={found.readable}/{len(found.events)}"
    )
    _events(found.events)
    _channels(found.channels)
    _tries(found.attempts)
    _stats(found)
    _shots(found)
    print(f"advice {found.hint}")


def _events(rows: Sequence[Event]) -> None:
    for item in rows:
        print(
            f"  {item.name:<26}{_val(item.id):>5}"
            f"  {item.state:<12}{item.reason or ''}"
        )


def _channels(rows: Sequence[Channel]) -> None:
    for item in rows:
        print(
            f"  {item.name:<8}{item.mode or '-':>6}"
            f"  {_right(item.writable):<11}{item.reason or ''}"
        )


def _right(writable: bool) -> str:
    """通道方向：可写才是注入通道，只读只是登记在册。"""
    return "writable" if writable else "read-only"


def _tries(rows: Sequence[Outcome]) -> None:
    for item in rows:
        print(f"  write {item.channel:<8}-> {_result(item)}")


def _result(item: Outcome) -> str:
    """一次试探写入的结果：送达即 accepted，否则取内核 errno 原文。"""
    if item.delivered:
        return "accepted"
    return item.error or "-"


def _stats(found: Report) -> None:
    stats = found.stats
    print(
        f"stats  cap_used={_val(stats.cap_used)}"
        f" stc_total={_val(stats.stc_total)} policy={stats.policy}"
    )
    for row in stats.tables:
        print(
            f"  {row.name:<16}{row.hook:<20} rows={row.rows} evals={row.evals}"
            f" allow={row.allow} deny={row.deny} audit={row.audit}"
        )


def _shots(found: Report) -> None:
    for index, shot in enumerate(found.samples, 1):
        print(
            f"sample {index} window={shot.window:g}s"
            f" hit={shot.hit} {shot.state}"
        )
        if shot.reason:
            print(f"  reason {shot.reason}")
    if found.mode == VERIFY:
        print(f"witnessed={found.witnessed}")


def _val(value: int | None) -> str:
    return "-" if value is None else str(value)


def _code(state: State) -> int:
    if state is State.OK:
        return 0
    if state is State.PARTIAL:
        return EXIT_PARTIAL
    return EXIT_FAIL