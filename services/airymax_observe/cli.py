# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 观测命令行入口
#
# 两个清点类别共用同一份出口约定。hardware 回答三个问题：这一台机器上都
# 有什么、哪些缺失、哪些被关着；firmware 回答驱动期望的固件文件名与本次
# 开机的实际载入情况。--json 与人类可读输出是同一份数据的两种呈现，门禁
# 读 --json、人读文本。
#
# 退出码：0 表示必检项齐全、没有设备被关断且没有固件失败证据；3 表示有
# 设备被 rfkill 关着，或有固件项缺证据（unknown 不等于通过）；1 表示必检
# 项缺失（cpu/block/net 至少一类整类不在），或有固件载入失败。2 为用法错误。

"""AirymaxOS observe command line (I-24)."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from . import fw, hw

__all__ = ["EXIT_FAIL", "EXIT_PARTIAL", "main"]

PROG = "airy-observe"

EXIT_FAIL = 1

EXIT_PARTIAL = 3

SHOWN = 4


def main(argv: Sequence[str] | None = None) -> int:
    args = _args(argv)
    if args.cmd == "firmware":
        return _fw_main(args)
    found = hw.collect(args.root)
    if args.json:
        print(json.dumps(found.dump(), ensure_ascii=False))
    else:
        _emit(found)
    return _code(found)


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=PROG, description="AirymaxOS 整机硬件与固件清点",
    )
    parser.add_argument(
        "cmd", nargs="?", default="hardware",
        choices=["hardware", "firmware"],
        help="清点类别（hardware 或 firmware）",
    )
    parser.add_argument(
        "--json", action="store_true", help="以 JSON 输出",
    )
    parser.add_argument(
        "--root", metavar="DIR", default="/sys", help="sysfs 挂载点",
    )
    parser.add_argument(
        "--modules", metavar="FILE", default=fw.MODULES,
        help="已载入模块表（默认 /proc/modules）",
    )
    parser.add_argument(
        "--dmesg", metavar="FILE", default=None,
        help="内核日志文件（默认读取 dmesg）",
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


def _fw_main(args: argparse.Namespace) -> int:
    found = fw.collect(fw.load_list(args.modules), fw.kernel_log(args.dmesg))
    if args.json:
        print(json.dumps(found.dump(), ensure_ascii=False))
    else:
        _emit_fw(found)
    return _fw_code(found)


def _emit_fw(found: fw.Survey) -> None:
    print(
        f"{PROG} firmware state={_fw_state(found)}"
        f" modules={len(found.modules)} files={len(found.items)}"
        f" loaded={found.count(fw.LOADED)} missing={found.count(fw.MISSING)}"
        f" unknown={found.count(fw.UNKNOWN)} warnings={len(found.warnings)}"
    )
    if not found.modules:
        print("  none")
    for mod in found.modules:
        items = found.items_of(mod)
        shown = [f"{item.file}={item.state}" for item in items
                 if item.state != fw.UNKNOWN][:SHOWN]
        rest = len(items) - len(shown)
        if not shown:
            body = "no-evidence"
        else:
            body = " ".join(shown) + (f" +{rest}" if rest else "")
        print(f"  {mod:<16}{len(items):>4}  {body}")
    for name in found.names(fw.MISSING):
        print(f"missing {name}")
    for line in found.warnings[:SHOWN]:
        print(f"warning {line}")


def _fw_state(found: fw.Survey) -> str:
    if found.count(fw.MISSING) or found.warnings:
        return "missing"
    return "partial" if found.count(fw.UNKNOWN) else "ok"


def _fw_code(found: fw.Survey) -> int:
    if found.count(fw.MISSING) or found.warnings:
        return EXIT_FAIL
    return EXIT_PARTIAL if found.count(fw.UNKNOWN) else 0
