# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# airy-bright 命令行
#
# status 只读评估，set 写绝对百分比，step 相对调节，config 打印参考。四个
# 子命令共用同一组路径与下限选项，缺省值来自 policy，测试与救援环境可以
# 整体改指临时目录而不改代码。
#
# 退出码：0 全部落地（只读时为全部可读）；3 部分落地或部分缺席；1 有设备
# 但全部失败；4 目标里一台设备都没有；2 用法错误。把 4 单列，是为了让上层
# 区分「KMS 还没接管」与「调好了」——这两件事在开机早期非常容易混淆。

"""AirymaxOS backlight command line."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from . import config, plan
from .policy import (
    DEFAULT_BACKLIGHT,
    DEFAULT_LEDS,
    MARK,
    SCREEN_FLOOR,
    Policy,
    Target,
)
from .report import Report, Row, State

__all__ = ["EXIT_FAIL", "EXIT_NA", "EXIT_PARTIAL", "EXIT_USAGE", "main"]

EXIT_FAIL = 1

EXIT_USAGE = 2

EXIT_PARTIAL = 3

EXIT_NA = 4

CODES = {
    State.OK: 0,
    State.PARTIAL: EXIT_PARTIAL,
    State.FAILED: EXIT_FAIL,
    State.ABSENT: EXIT_NA,
}


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行并分派；返回值即进程退出码。"""
    args = _args(argv)
    policy = Policy(backlight=args.backlight, leds=args.leds,
                    floor=args.floor)
    if args.cmd == "config":
        print(config.reference(policy))
        return 0
    bad = _validate(args)
    if bad is not None:
        return bad
    found = _run(args, policy)
    _emit(found, args.json)
    return CODES[found.state]


def _opt() -> argparse.ArgumentParser:
    """每次新建一份公共选项，避免父解析器共享 action 对象。"""
    opt = argparse.ArgumentParser(add_help=False)
    opt.add_argument("--json", action="store_true",
                     default=argparse.SUPPRESS, help="以 JSON 输出")
    opt.add_argument("--backlight", default=argparse.SUPPRESS,
                     help="backlight 类目录")
    opt.add_argument("--leds", default=argparse.SUPPRESS, help="leds 类目录")
    opt.add_argument("--floor", type=int, default=argparse.SUPPRESS,
                     help="屏幕下限百分比，0 允许关屏")
    return opt


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="airy-bright",
        parents=[_opt()],
        description="AirymaxOS 屏幕与键盘背光调节",
    )
    parser.set_defaults(
        json=False,
        backlight=DEFAULT_BACKLIGHT,
        leds=DEFAULT_LEDS,
        floor=SCREEN_FLOOR,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    targets = [item.value for item in Target]
    status = sub.add_parser("status", parents=[_opt()], help="只读评估背光")
    status.add_argument("target", nargs="?", choices=targets,
                        default=Target.ALL.value)
    put = sub.add_parser("set", parents=[_opt()], help="写入百分比并回读")
    put.add_argument("target", choices=targets)
    put.add_argument("pct", type=int, help="目标百分比 0..100")
    move = sub.add_parser("step", parents=[_opt()], help="相对调节并回读")
    move.add_argument("target", choices=targets)
    move.add_argument("delta", type=int, help="增量百分比，可为负")
    sub.add_parser("config", parents=[_opt()], help="打印路径与判定参考")
    return parser.parse_args(argv)


def _validate(args: argparse.Namespace) -> int | None:
    """下限与百分比在这里一次判死，避免打字错误被静默夹成边界值。"""
    if not 0 <= args.floor <= 99:
        return _reject("--floor 必须落在 0..99")
    pct = getattr(args, "pct", None)
    if pct is not None and not 0 <= pct <= 100:
        return _reject("百分比必须落在 0..100")
    return None


def _reject(reason: str) -> int:
    print(f"{MARK} usage-error {reason}", flush=True)
    return EXIT_USAGE


def _run(args: argparse.Namespace, policy: Policy) -> Report:
    target = Target(args.target)
    if args.cmd == "set":
        return plan.apply(policy, target, args.pct)
    if args.cmd == "step":
        return plan.step(policy, target, args.delta)
    return plan.read(policy, target)


def _emit(found, as_json: bool) -> None:
    if as_json:
        print(json.dumps(found.dump(), ensure_ascii=False, indent=2,
                         sort_keys=True))
        return
    print(f"{MARK} state={found.state.value} mode={found.mode}"
          f" target={found.target} floor={found.floor}%"
          f" landed={found.landed}/{found.present}")
    for row in found.rows:
        print("  " + _line(row))
    print(f"  advice {config.advice(found.state)}")


def _line(row: Row) -> str:
    if not row.present:
        return f"{row.kind:<8} -  absent"
    text = (f"{row.kind:<8} {row.name} type={row.type}"
            f" {_pct(row.pct)} {_raw(row.raw, row.maximum)}")
    if row.want is not None:
        text += f" want={row.want}"
    if row.error is not None:
        return f"{text} error={row.error}"
    return f"{text} landed" if row.landed else text


def _pct(pct: int | None) -> str:
    return "-" if pct is None else f"{pct}%"


def _raw(raw: int | None, maximum: int) -> str:
    return "-" if raw is None else f"{raw}/{maximum}"
