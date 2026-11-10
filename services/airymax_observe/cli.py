# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 观测命令行入口
#
# 三个清点类别共用同一份出口约定。hardware 回答三个问题：这一台机器上都
# 有什么、哪些缺失、哪些被关着；firmware 回答驱动期望的固件文件名与本次
# 开机的实际载入情况；system 回答系统自身是否自洽：有没有失败单元、本次
# 开机有没有 err 日志、实际装的包与镜像交付的清单差在哪。--json 与人类
# 可读输出是同一份数据的两种呈现，门禁读 --json、人读文本。
#
# 退出码：0 表示必检项齐全、没有设备被关断、没有固件失败证据、系统三面
# 全部干净；3 表示缺证据（unknown 不等于通过）；1 表示出现失败证据——
# 必检项整类不在、固件载入失败、单元 failed、err 日志越界或包集合漂移。
# 2 为用法错误。
#
# system 的 err 日志按归属分四层：产品自有单元（airymax-、airy- 前缀）、任何
# 崩溃行（dumped core）与读不到归属的条目一律判失败；平台层（固件表与热区
# 诊断）与基底层（上游组件）各有一份预算，互不挪用。预算来自 `--budget-file`
# 指向的分组文件：按机器类别取组，平台条目再按机器指纹过滤，缺组即判负。
# 不给文件时两层额度都是 0，即「外来 err 也必须为零」的最严口径。

"""AirymaxOS observe command line (I-24, J-31)."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence

from . import fw, hw, sys as osys

__all__ = ["EXIT_FAIL", "EXIT_PARTIAL", "main"]

PROG = "airy-observe"

EXIT_FAIL = 1

EXIT_PARTIAL = 3

# 明细行的上限。曾固定为 4，结果 T23 报 errors=42 却只列 4 条，现场看不出
# 另外 38 条是什么——计数与明细必须能对上，否则这一行就失去了诊断价值。
# 取值按两层预算之和的三倍留余量：正常失败现场一次列全，真出现雪崩时仍然
# 截断并显式告知剩余条数，不让日志淹没有效信息。
SHOWN = 80


def _rows(label: str, items: Sequence[str]) -> None:
    """逐条打印明细；超出上限时补一行剩余条数，不静默丢弃。"""
    for item in items[:SHOWN]:
        print(f"{label} {item}")
    if len(items) > SHOWN:
        print(f"{label} ... {len(items) - SHOWN} more")


def main(argv: Sequence[str] | None = None) -> int:
    args = _args(argv)
    if args.cmd == "firmware":
        return _fw_main(args)
    if args.cmd == "system":
        return _sys_main(args)
    found = hw.collect(args.root)
    if args.json:
        print(json.dumps(found.dump(), ensure_ascii=False))
    else:
        _emit(found)
    return _code(found)


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=PROG, description="AirymaxOS 整机硬件、固件与系统完整性清点",
    )
    parser.add_argument(
        "cmd", nargs="?", default="hardware",
        choices=["hardware", "firmware", "system"],
        help="清点类别（hardware、firmware 或 system）",
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
    parser.add_argument(
        "--manifest", metavar="FILE", default=osys.MANIFEST,
        help="镜像交付的包清单（默认 /etc/airymaxos-packages）",
    )
    parser.add_argument(
        "--units", metavar="FILE", default=None,
        help="systemctl --failed 的输出文件（默认现场执行）",
    )
    parser.add_argument(
        "--journal", metavar="FILE", default=None,
        help="journalctl -p err -b 的输出文件（默认现场执行）",
    )
    parser.add_argument(
        "--rpm", metavar="FILE", default=None,
        help="rpm -qa 的包名清单文件（默认现场执行）",
    )
    parser.add_argument(
        "--budget-file", metavar="FILE", default=None,
        help="分层 err 预算文件（按机器类别分组；不给即两层额度均为 0）",
    )
    parser.add_argument(
        "--class", metavar="CLS", dest="cls", default="auto",
        choices=["auto", "qemu", "real"],
        help="机器类别（默认按 DMI 判定；测试可显式指定）",
    )
    parser.add_argument(
        "--machine-fp", metavar="FP", default=None,
        help="机器指纹（默认按 DMI 计算；测试可显式指定）",
    )
    parser.add_argument(
        "--exclude", metavar="UNIT", action="append", default=[],
        help="不计入判定两侧的单元（可重复）；供判定者排除自己承载的单元",
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


def _sys_main(args: argparse.Namespace) -> int:
    found = osys.collect(
        _source(args.units, osys.units_live),
        _source(args.journal, osys.journal_live),
        _source(args.rpm, osys.rpm_live),
        osys.read_text(args.manifest),
        _budget(args),
        tuple(args.exclude),
    )
    if args.json:
        print(json.dumps(found.dump(), ensure_ascii=False))
    else:
        _emit_sys(found)
    return _sys_code(found)


def _budget(args: argparse.Namespace) -> osys.Budget:
    """按当前机器的类别与指纹取一组预算；文件读不到即整组判负。"""
    values = osys.dmi_live(os.path.join(args.root, osys.DMI_REL))
    cls = osys.machine_class(values) if args.cls == "auto" else args.cls
    fp = args.machine_fp or osys.machine_fp(values)
    if not args.budget_file:
        return osys.Budget(cls)
    text = osys.read_text(args.budget_file)
    if text is None:
        return osys.Budget(cls, read=False)
    return osys.budget_for(osys.parse_budget(text, fp), cls)


def _source(path: str | None, live) -> str | None:
    """给定了文件就只读文件（读不到即 unknown），否则现场执行。"""
    return live() if path is None else osys.read_text(path)


def _tag(err: osys.Err) -> str:
    """err 明细的层标：自有缺陷与平台噪声要能一眼分开，其余层不加标。"""
    if err.owned:
        return f"{err.text} [owned]"
    if err.platform:
        return f"{err.text} [platform]"
    return err.text


def _budget_label(bud: osys.Budget) -> str:
    """预算的可读形态；整组不成立时写明缺失的类别，不假装额度为 0。"""
    if not bud.read:
        return f"missing:{bud.cls}"
    return f"platform:{bud.platform}/base:{bud.base}"


def _emit_sys(found: osys.Survey) -> None:
    # 首行是 TAP 证据行：判据的每个分量都在这一行里，现场比对不需要再翻
    # 明细。unknown 单列，因为它既不是「干净」也不是「自有缺陷」。
    print(
        f"{PROG} system state={found.state} units={len(found.units)}"
        f" errors={len(found.errors)} owned={found.owned_n}"
        f" platform={found.platform_n} base={found.base_n}"
        f" unknown={found.unknown_n} class={found.budget.cls}"
        f" budget={_budget_label(found.budget)}"
        f" packages={found.packs_state}"
        f" missing={len(found.missing)} extra={len(found.extra)}"
    )
    print(f"  {'units':<9}{found.units_state:<8}{len(found.units):>5}"
          f" failed")
    print(f"  {'logs':<9}{found.logs_state:<8}{len(found.errors):>5}"
          f" errors  owned={found.owned_n} platform={found.platform_n}"
          f" base={found.base_n} unknown={found.unknown_n}"
          f" budget={_budget_label(found.budget)}")
    print(f"  {'packages':<9}{found.packs_state:<8}{found.have_n:>5}"
          f" installed  declared={found.want_n}")
    _rows("failed", found.units)
    _rows("error", [_tag(err) for err in found.errors])
    _rows("missing", found.missing)
    _rows("extra", found.extra)


def _sys_code(found: osys.Survey) -> int:
    if found.packs_state == osys.STATE_UNKNOWN:
        return EXIT_PARTIAL
    if found.state in (osys.STATE_OK, osys.STATE_UNKNOWN):
        return EXIT_PARTIAL if found.state == osys.STATE_UNKNOWN else 0
    return EXIT_FAIL
