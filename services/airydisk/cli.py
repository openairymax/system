# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 磁盘命令行入口
#
# 三个子命令对应契约里的一张表：status 出拓扑、check 出护栏判定、
# apply 执行计划并要求 --confirm 与计划盘名逐字一致。JSON 输出与人类
# 输出共用同一份数据，判定 token 不翻译成自由文本后再被解析；退出码
# 0 成功、1 执行失败、2 用法错误、3 拒绝执行，供门禁直接判读。

"""AirymaxOS disk command line (M-31).

设计依据：docs-linux/190-distribution/03-installer-design.md §3 与
0.0.4 方案 §6.3 M-31。``airy-disk --json status`` 输出拓扑快照，
``--json check`` 输出拒绝 token，``--json apply`` 输出逐步结果。

"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from . import model, ops, probe, runner

__all__ = ["EXIT_FAIL", "EXIT_REFUSED", "EXIT_USAGE", "main"]

EXIT_FAIL = 1

EXIT_USAGE = 2

EXIT_REFUSED = 3

GiB = 1024**3

MiB = 1024**2


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行并分派；返回值即进程退出码。"""
    args = _args(argv)
    return _HANDLERS[args.cmd](args)


def _json_opt() -> argparse.ArgumentParser:
    """每次新建一份 --json，避免父解析器共享 action 对象。

    argparse 的 parents 只拷贝 action 引用，主解析器上的默认值会被
    子解析器继承，从而在 ``--json`` 位于子命令之前时把它重置为假。
    各解析器各自持有独立 action，子解析器的默认值才能保持 SUPPRESS。
    """
    opt = argparse.ArgumentParser(add_help=False)
    opt.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="以 JSON 输出",
    )
    return opt


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="airy-disk",
        parents=[_json_opt()],
        description="AirymaxOS 磁盘管理",
    )
    parser.set_defaults(json=False)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", parents=[_json_opt()], help="列出磁盘拓扑")
    check = sub.add_parser("check", parents=[_json_opt()], help="判定能否改写")
    check.add_argument("--disk", required=True, metavar="NAME")
    act = sub.add_parser("apply", parents=[_json_opt()], help="执行磁盘计划")
    act.add_argument("--plan", required=True, metavar="FILE")
    act.add_argument(
        "--confirm",
        required=True,
        metavar="NAME",
        help="二次确认：与计划中的磁盘名逐字一致",
    )
    return parser.parse_args(argv)


def _status(args: argparse.Namespace) -> int:
    topo = probe.probe()
    if args.json:
        print(json.dumps(model.dump(topo), ensure_ascii=False))
        return 0
    _show(topo)
    return 0


def _check(args: argparse.Namespace) -> int:
    topo = probe.probe()
    disk = _find(topo, args.disk)
    if disk is None:
        return _err(f"未找到磁盘 {args.disk}", EXIT_USAGE)
    verdict = ops.refuse(disk, _hosts(topo))
    if args.json:
        print(json.dumps({"disk": disk.name, "refusal": _token(verdict)}))
    else:
        print("ok" if verdict is None else ops.REFUSAL_TEXT[verdict])
    return 0 if verdict is None else EXIT_REFUSED


def _apply(args: argparse.Namespace) -> int:
    plan = _load(args.plan)
    if plan is None:
        return EXIT_USAGE
    if args.confirm != plan.disk:
        return _err(f"确认名 {args.confirm} 与计划盘 {plan.disk} 不符",
                    EXIT_USAGE)
    topo = probe.probe()
    disk = _find(topo, plan.disk)
    if disk is None:
        return _err(f"未找到磁盘 {plan.disk}", EXIT_USAGE)
    verdict = ops.refuse(disk, _hosts(topo))
    if verdict is not None:
        return _err(ops.REFUSAL_TEXT[verdict], EXIT_REFUSED)
    if os.geteuid() != 0:
        return _err("改写磁盘需要 root 权限", EXIT_REFUSED)
    outcome = runner.apply(plan)
    _report(outcome, args.json)
    return 0 if outcome.ok else EXIT_FAIL


def _load(path: str) -> ops.Plan | None:
    """从文件或标准输入读取计划；任何解析问题都转成可读的用法错误。"""
    try:
        text = sys.stdin.read() if path == "-" else _read(path)
        plan = ops.load(json.loads(text))
    except (OSError, ValueError) as exc:
        _err(f"计划不可用：{exc}", EXIT_USAGE)
        return None
    return plan


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _find(topo: model.Topology, name: str) -> model.Disk | None:
    return next((disk for disk in topo.disks if disk.name == name), None)


def _hosts(topo: model.Topology) -> str | None:
    """承载运行系统的磁盘名；安装介质里没有则为 None。"""
    disk = probe.host(topo, "/")
    return None if disk is None else disk.name


def _token(verdict: ops.Refusal | None) -> str | None:
    return None if verdict is None else verdict.value


def _err(message: str, code: int) -> int:
    print(f"airy-disk: {message}", file=sys.stderr)
    return code


def _show(topo: model.Topology) -> None:
    for disk in topo.disks:
        print(f"{disk.name}  {_size(disk.size)}  {disk.bus.value}  "
              f"{disk.table.value or '-'}{_flags(disk)}")
        for part in disk.parts:
            print(f"  {part.name}  {_size(part.size)}  "
                  f"{part.fs.value or '-'}  {part.role.value}  "
                  f"{part.mount or '-'}")


def _flags(disk: model.Disk) -> str:
    marks = [
        name
        for name, on in (("ro", disk.ro), ("rm", disk.removable))
        if on
    ]
    return "" if not marks else "  " + " ".join(marks)


def _size(num: int) -> str:
    if num >= GiB:
        return f"{num / GiB:.1f}G"
    if num >= MiB:
        return f"{num / MiB:.0f}M"
    return f"{num}B"


def _report(outcome: runner.Outcome, as_json: bool) -> None:
    if as_json:
        print(json.dumps(_obj(outcome), ensure_ascii=False))
        return
    for item in outcome.done:
        print(f"ok    {' '.join(item.argv)}")
    for item in outcome.undone:
        print(f"undo  {' '.join(item.argv)}")
    if outcome.failed is not None:
        print(f"FAIL  rc={outcome.failed.rc} "
              f"{' '.join(outcome.failed.step.argv)}")
        print(outcome.failed.err, end="")
    print("未完成" if outcome.failed is not None else "完成")


def _obj(outcome: runner.Outcome) -> dict[str, object]:
    failed = outcome.failed
    return {
        "disk": outcome.plan.disk,
        "ok": outcome.ok,
        "unclean": outcome.unclean,
        "done": [list(item.argv) for item in outcome.done],
        "undone": [list(item.argv) for item in outcome.undone],
        "failed": None if failed is None else {
            "rc": failed.rc,
            "argv": list(failed.step.argv),
            "err": failed.err,
        },
    }


_HANDLERS = {
    "status": _status,
    "check": _check,
    "apply": _apply,
}
