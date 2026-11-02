# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# airy-power 命令行
#
# status 只读评估，apply 落地并回读，config 打印档位与门限参考。三者共用
# 同一套路径选项，缺省值来自 policy 模块，测试与救援环境可以整体改指到
# 临时目录而不用改代码。只读评估不写节流采样，连续巡检不会吃掉一次真实的
# 降档判据。
#
# 退出码：0 档位落地且严重度未到 hot；3 档位部分落地或严重度已达 hot 以上
# （温度或节流增速任一够格）；1 存在控制点但全部写入失败；2 用法错误。守护
# 单元用 SuccessExitStatus=3 把「已降档但机器仍热」与真正的失败分开，避免
# 过热时每个周期都触发 incident。

"""AirymaxOS power command line."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from . import config, guard
from .policy import (
    DEFAULT_CHOICES,
    DEFAULT_CPUFREQ,
    DEFAULT_PROFILE,
    DEFAULT_STATE,
    DEFAULT_SUPPLY,
    DEFAULT_THERMAL,
    DEFAULT_THROTTLE,
    MARK,
    Policy,
)
from .report import Report
from .thermal import Severity, Zone
from .tuning import Knob, State

__all__ = ["EXIT_FAIL", "EXIT_PARTIAL", "main"]

EXIT_FAIL = 1

EXIT_PARTIAL = 3


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行并分派；返回值即进程退出码。"""
    args = _args(argv)
    policy = _policy(args)
    if args.cmd == "config":
        print(config.reference(policy))
        return 0
    if args.cmd == "apply":
        found = guard.apply(policy)
    else:
        found = guard.inspect(policy)
    _emit(found, args.json)
    return _code(found)


def _opt() -> argparse.ArgumentParser:
    """每次新建一份公共选项，避免父解析器共享 action 对象。"""
    opt = argparse.ArgumentParser(add_help=False)
    opt.add_argument("--json", action="store_true",
                     default=argparse.SUPPRESS, help="以 JSON 输出")
    for flag, label in _paths():
        opt.add_argument(flag, default=argparse.SUPPRESS, help=label)
    return opt


def _paths() -> tuple[tuple[str, str], ...]:
    return (
        ("--profile", "platform_profile 节点"),
        ("--choices", "platform_profile_choices 节点"),
        ("--cpufreq", "cpufreq 根目录"),
        ("--supply", "power_supply 类目录"),
        ("--thermal", "thermal 类目录"),
        ("--throttle", "cpu0/thermal_throttle 目录"),
        ("--state", "节流增速采样文件"),
    )


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="airy-power",
        parents=[_opt()],
        description="AirymaxOS 电源与温度档位管理",
    )
    parser.set_defaults(
        json=False,
        profile=DEFAULT_PROFILE,
        choices=DEFAULT_CHOICES,
        cpufreq=DEFAULT_CPUFREQ,
        supply=DEFAULT_SUPPLY,
        thermal=DEFAULT_THERMAL,
        throttle=DEFAULT_THROTTLE,
        state=DEFAULT_STATE,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", parents=[_opt()], help="只读评估当前档位")
    sub.add_parser("apply", parents=[_opt()], help="写入档位并回读")
    sub.add_parser("config", parents=[_opt()], help="打印档位与门限参考")
    return parser.parse_args(argv)


def _policy(args: argparse.Namespace) -> Policy:
    return Policy(
        profile=args.profile,
        choices=args.choices,
        cpufreq=args.cpufreq,
        supply=args.supply,
        thermal=args.thermal,
        throttle=args.throttle,
        state=args.state,
    )


def _code(found: Report) -> int:
    if found.state is State.FAILED:
        return EXIT_FAIL
    if found.state is State.PARTIAL:
        return EXIT_PARTIAL
    if found.severity.rank >= Severity.HOT.rank:
        return EXIT_PARTIAL
    return 0


def _emit(found: Report, as_json: bool) -> None:
    if as_json:
        print(json.dumps(found.dump(), ensure_ascii=False, indent=2,
                         sort_keys=True))
        return
    print(f"{MARK} state={found.state.value} source={found.source.value}"
          f" charge={_percent(found.charge)}"
          f" severity={found.severity.value}"
          f" rate={_speed(found.rate)}"
          f" landed={found.landed}/{found.present}")
    for zone in found.heat.zones:
        print(f"  zone {zone.name} {zone.celsius:.1f}C"
              f" critical={_limit(zone)} mode={zone.mode or '-'}"
              f" severity={zone.severity.value}")
    print(f"  throttle package={found.heat.package} core={found.heat.core}")
    for knob in found.knobs:
        print(f"  {knob.name:<20} target={knob.target or '-':<16}"
              f" current={knob.current or '-':<16} {_flag(knob)}")
    print(f"  advice {config.advice(found.state)}")
    print(f"  thermal {config.temp_advice(found.severity)}")


def _percent(charge: int | None) -> str:
    return "-" if charge is None else f"{charge}%"


def _speed(rate: float | None) -> str:
    return "-" if rate is None else f"{rate:.2f}/s"


def _limit(zone: Zone) -> str:
    return "-" if zone.critical is None else f"{zone.critical / 1000:.2f}C"


def _flag(knob: Knob) -> str:
    if not knob.present:
        return "skip"
    return "ok" if knob.landed else "miss"
