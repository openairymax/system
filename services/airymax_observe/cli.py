# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 硬件清点命令行入口
#
# 一条命令回答三个问题：这一台机器上都有什么、哪些缺失、哪些被关着。
# --json 与人类可读输出是同一份数据的两种呈现，门禁读 --json、人读文本。
#
# 退出码：0 表示必检项齐全且没有设备被关断；3 表示有设备存在但被 rfkill
# 关着（可用 --unblock 之外的显式操作启用，本命令只报告）；1 表示必检项
# 缺失（cpu/block/net 至少一类整类不在）。2 为用法错误。

"""AirymaxOS observe command line (I-24)."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from . import hw

__all__ = ["EXIT_FAIL", "EXIT_PARTIAL", "main"]

PROG = "airy-observe"

EXIT_FAIL = 1

EXIT_PARTIAL = 3

SHOWN = 4


def main(argv: Sequence[str] | None = None) -> int:
    args = _args(argv)
    found = hw.collect(args.root)
    if args.json:
        print(json.dumps(found.dump(), ensure_ascii=False))
    else:
        _emit(found)
    return _code(found)


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=PROG, description="AirymaxOS 整机硬件清点",
    )
    parser.add_argument(
        "cmd", nargs="?", default="hardware", choices=["hardware"],
        help="清点类别（当前只有 hardware）",
    )
    parser.add_argument(
        "--json", action="store_true", help="以 JSON 输出",
    )
    parser.add_argument(
        "--root", metavar="DIR", default="/sys", help="sysfs 挂载点",
    )
    return parser.parse_args(argv)


def _emit(found: hw.Survey) -> None:
    print(
        f"{PROG} state={_state(found)} groups={len(found.groups)}"
        f" devices={found.total} missing={len(found.missing)}"
        f" blocked={len(found.blocked)}"
    )
    for group in found.groups:
        print(f"  {group.name:<9}{len(group.entries):>4}  {_names(group)}")
    if found.missing:
        print(f"missing {', '.join(found.missing)}")
    if found.blocked:
        print(f"blocked {', '.join(found.blocked)}")


def _names(group: hw.Group) -> str:
    if group.absent:
        return "absent"
    items = [f"{entry.name}={entry.state}" for entry in group.entries]
    rest = "" if len(items) <= SHOWN else f" +{len(items) - SHOWN}"
    return " ".join(items[:SHOWN]) + rest


def _state(found: hw.Survey) -> str:
    if found.missing:
        return "missing"
    return "partial" if found.blocked else "ok"


def _code(found: hw.Survey) -> int:
    if found.missing:
        return EXIT_FAIL
    return EXIT_PARTIAL if found.blocked else 0