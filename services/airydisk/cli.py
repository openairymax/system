# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 磁盘命令行入口
#
# 八个子命令对应契约里的一张表：status 出拓扑、layout 出系统与数据分区
# 的布局判定（--fstab 一并判定已装系统的持久挂载）、persist 单独判定一份
# fstab（装机 chroot 内没有可信拓扑，只能判持久挂载）、check 出护栏判定、
# apply 执行计划并要求 --confirm 与计划盘名逐字一致、mount 在 agentrt 之前
# 把 /data 挂上、grow 把数据分区扩到盘尾、health 判 /data 的挂载、只读与
# 容量。破坏性的 apply 与 grow 都要 --dry-run 先行，grow 另要 --confirm
# 与计划盘名逐字一致。JSON 输出与人类输出共用同一份数据，判定 token 不
# 翻译成自由文本后再被解析；退出码 0 成功、1 执行失败、2 用法错误、3 拒绝
# 执行或判定未通过、4 本机未启用数据分区（不适用，不是故障），供门禁直接
# 判读。mount 的 4 只在没有安装期选盘记录时出现：0.0.3 及更早的系统没有
# 数据分区，把「未启用」报成「挂载失败」会让每次开机都留一个假故障。
#
# status 同时把现场拓扑与安装期选盘记录比对（判据③）：记录里是哪块盘、
# 多大、什么总线，与此刻探测到的同一块盘逐字段核对，结论以 match /
# mismatch / absent / invalid 四个 token 之一出现在 install 段。status 与
# layout 另接受 --snapshot：重放一份已保存的 status 输出，让 CI 在没有受
# 测机器可用时也能复算同一份证据。

"""AirymaxOS disk command line (M-31).

设计依据：docs-linux/190-distribution/03-installer-design.md §3 与
0.0.4 方案 §6.3 M-31、K-24。``airy-disk --json status`` 输出拓扑快照与
安装期选盘记录的比对，``--json check`` 输出拒绝 token，``--json apply``
输出逐步结果，``--json mount``/``grow``/``health`` 输出判定 token 与
修复建议。

"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from . import (
    fstab,
    grow,
    health,
    layout,
    model,
    mount,
    ops,
    probe,
    record,
    runner,
)

__all__ = ["EXIT_FAIL", "EXIT_NA", "EXIT_REFUSED", "EXIT_USAGE", "main"]

EXIT_FAIL = 1

EXIT_USAGE = 2

EXIT_REFUSED = 3

EXIT_NA = 4

GiB = 1024**3

MiB = 1024**2

DEFAULT_FSTAB = "/etc/fstab"


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


def _snap_opt() -> argparse.ArgumentParser:
    """只读子命令的 --snapshot：复算一份已保存的 status 输出。"""
    opt = argparse.ArgumentParser(add_help=False)
    opt.add_argument(
        "--snapshot",
        metavar="FILE",
        help="复算一份 status 快照，- 为标准输入；缺省探测本机",
    )
    return opt


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="airy-disk",
        parents=[_json_opt()],
        description="AirymaxOS 磁盘管理",
    )
    parser.set_defaults(json=False, snapshot=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", parents=[_json_opt(), _snap_opt()],
                   help="列出磁盘拓扑")
    lay = sub.add_parser("layout", parents=[_json_opt(), _snap_opt()],
                         help="判定系统与数据分区布局")
    lay.add_argument(
        "--fstab",
        metavar="FILE",
        help="一并判定已安装系统的 /etc/fstab 能否持久挂载",
    )
    per = sub.add_parser(
        "persist",
        parents=[_json_opt()],
        help="只判定一份 fstab 能否持久挂载",
    )
    per.add_argument(
        "--file",
        default=DEFAULT_FSTAB,
        metavar="FILE",
        help=f"fstab 路径，缺省 {DEFAULT_FSTAB}",
    )
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
    act.add_argument(
        "--dry-run",
        action="store_true",
        help="只复算并列出步骤，不执行任何外部命令",
    )
    mnt = sub.add_parser(
        "mount",
        parents=[_json_opt()],
        help="按 fstab 挂载 /data",
    )
    mnt.add_argument(
        "--file",
        default=DEFAULT_FSTAB,
        metavar="FILE",
        help=f"fstab 路径，缺省 {DEFAULT_FSTAB}",
    )
    mnt.add_argument(
        "--dry-run",
        action="store_true",
        help="只判定并列出步骤，不执行 mount",
    )
    grw = sub.add_parser(
        "grow",
        parents=[_json_opt(), _snap_opt()],
        help="把 /data 分区扩容到盘尾",
    )
    grw.add_argument(
        "--confirm",
        metavar="NAME",
        help="二次确认：与计划中的磁盘名逐字一致",
    )
    grw.add_argument(
        "--dry-run",
        action="store_true",
        help="只复算并列出步骤，不执行任何外部命令",
    )
    sub.add_parser("health", parents=[_json_opt()], help="判定 /data 健康")
    return parser.parse_args(argv)


def _status(args: argparse.Namespace) -> int:
    topo = _snap(args)
    if topo is None:
        return EXIT_USAGE
    install = record.load()
    agree = record.compare(install, topo)
    if args.json:
        payload = model.dump(topo)
        payload["install"] = record.dump(install, agree)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    _show(topo)
    _install(install, agree)
    return 0


def _layout(args: argparse.Namespace) -> int:
    topo = _snap(args)
    if topo is None:
        return EXIT_USAGE
    found = layout.inspect(topo)
    persist = None
    if args.fstab:
        persist = _persist(args.fstab)
        if persist is None:
            return EXIT_USAGE
    _layout_out(found, persist, args.json)
    if not found.ok or (persist is not None and not persist.ok):
        return EXIT_REFUSED
    return 0


def _layout_out(found: layout.Layout, persist: fstab.Persist | None,
                as_json: bool) -> None:
    payload = layout.dump(found)
    if persist is not None:
        payload.update(fstab.dump(persist))
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
        return
    tail = "" if persist is None else f"  persist={persist.state.value}"
    print(f"layout  {found.kind}  {found.state.value}  "
          f"root={found.root or '-'} data={found.data or '-'}{tail}")


def _mount_out(found: mount.Mount, as_json: bool,
               dry: bool = False) -> None:
    payload = mount.dump(found)
    if as_json:
        if dry:
            payload["dry_run"] = True
        print(json.dumps(payload, ensure_ascii=False))
        return
    print(f"mount  {found.state.value}  {found.source or '-'}")
    if dry and found.step is not None:
        print(f"plan  {found.step.action.value}  {found.step.target}  "
              f"{' '.join(found.step.argv)}")
    elif found.advice:
        print(found.advice)


def _grow_out(found: grow.Grow, as_json: bool,
              dry: bool = False) -> None:
    payload = grow.dump(found)
    if as_json:
        if dry:
            payload["dry_run"] = True
        print(json.dumps(payload, ensure_ascii=False))
        return
    print(f"grow  {found.state.value}  {found.disk or '-'}  "
          f"{found.part or '-'}  {found.fs.value or '-'}")
    if dry and found.plan is not None:
        _dry(found.plan, False)
    elif found.advice:
        print(found.advice)


def _persist_cmd(args: argparse.Namespace) -> int:
    """只判一份 fstab：装机 chroot 内拓扑不可信，持久挂载仍可判。"""
    found = _persist(args.file)
    if found is None:
        return EXIT_USAGE
    if args.json:
        print(json.dumps(fstab.dump(found), ensure_ascii=False))
    else:
        print(f"persist={found.state.value}")
    return 0 if found.ok else EXIT_REFUSED


def _persist(path: str) -> fstab.Persist | None:
    """读一份 fstab 取证；读不到按用法错误处理，不冒充判定不通过。"""
    text = _fstab(path)
    if text is None:
        return None
    return fstab.check(text)


def _fstab(path: str) -> str | None:
    """读 fstab 文本；路径不可用时报用法错误并返回 None。"""
    try:
        return _read(path)
    except OSError as exc:
        _err(f"fstab 不可用：{exc}", EXIT_USAGE)
        return None


def _snap(args: argparse.Namespace) -> model.Topology | None:
    """取现场拓扑；给了 --snapshot 就复算快照，不探测本机。

    受测机器不在 runner 上，CI 只能重放 285H 实机取证的 status 输出。
    """
    if not args.snapshot:
        return probe.probe()
    try:
        if args.snapshot == "-":
            text = sys.stdin.read()
        else:
            text = _read(args.snapshot)
        return model.load(json.loads(text))
    except (OSError, ValueError) as exc:
        _err(f"快照不可用：{exc}", EXIT_USAGE)
        return None


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
    if args.dry_run:
        _dry(plan, args.json)
        return 0
    outcome = runner.apply(plan)
    _report(outcome, args.json)
    return 0 if outcome.ok else EXIT_FAIL


def _mount_cmd(args: argparse.Namespace) -> int:
    """在 agentrt 之前把 /data 挂上；dry-run 只判不挂。"""
    text = _fstab(args.file)
    if text is None:
        return EXIT_USAGE
    declared = record.load().known
    found = mount.planned(text, declared)
    if args.dry_run:
        _mount_out(found, args.json, dry=True)
        if found.state is mount.State.UNSET:
            return EXIT_NA
        return 0 if found.ok or found.step is not None else EXIT_REFUSED
    if found.step is not None and os.geteuid() != 0:
        return _err("挂载数据分区需要 root 权限", EXIT_REFUSED)
    if found.step is not None:
        found = mount.ensure(text, runner=runner.run, declared=declared)
    _mount_out(found, args.json)
    if found.state is mount.State.FAILED:
        return EXIT_FAIL
    if found.state is mount.State.UNSET:
        return EXIT_NA
    return 0 if found.ok else EXIT_REFUSED


def _grow(args: argparse.Namespace) -> int:
    """把 /data 扩到盘尾；判定不通过时只给建议，不生成计划。"""
    topo = _snap(args)
    if topo is None:
        return EXIT_USAGE
    found = grow.plan(topo)
    if not found.ok:
        _grow_out(found, args.json)
        return EXIT_REFUSED
    if args.dry_run:
        _grow_out(found, args.json, dry=True)
        return 0
    plan = found.plan
    if plan is None:
        return _err("扩容判定缺少计划", EXIT_USAGE)
    if args.confirm != plan.disk:
        return _err(f"确认名 {args.confirm or '-'} 与计划盘 "
                    f"{plan.disk} 不符", EXIT_USAGE)
    if os.geteuid() != 0:
        return _err("扩容数据分区需要 root 权限", EXIT_REFUSED)
    outcome = runner.apply(plan)
    _report(outcome, args.json)
    if outcome.failed is not None:
        advice = grow.repair(outcome.failed.step, outcome.failed.rc)
        print(f"airy-disk: {advice}", file=sys.stderr)
    return 0 if outcome.ok else EXIT_FAIL


def _health(args: argparse.Namespace) -> int:
    """判定 /data 的挂载、只读与容量；不通过即拒绝。"""
    found = health.inspect(probe.probe())
    if args.json:
        print(json.dumps(health.dump(found), ensure_ascii=False))
    else:
        print(f"health  {found.state.value}  {found.part or '-'}  "
              f"{found.used}%")
        if found.advice:
            print(found.advice)
    return 0 if found.ok else EXIT_REFUSED


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


def _install(install: record.Install, agree: record.Agree) -> None:
    """打印安装期记录比对；不一致时把差异字段一并列出。"""
    line = (f"install  {install.disk or '-'}  {install.layout or '-'}  "
            f"{agree.verdict.value}")
    if agree.diff:
        line = f"{line}  diff={','.join(agree.diff)}"
    print(line)


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


def _dry(plan: ops.Plan, as_json: bool) -> None:
    """复算并列出计划步骤，不触碰设备。"""
    if as_json:
        print(json.dumps({"dry_run": True, "plan": ops.dump(plan)},
                         ensure_ascii=False))
        return
    for step in plan.steps:
        print(f"plan  {step.action.value}  {step.target}  "
              f"{' '.join(step.argv)}")
    print("dry-run：未执行任何命令")


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
    "layout": _layout,
    "persist": _persist_cmd,
    "check": _check,
    "apply": _apply,
    "mount": _mount_cmd,
    "grow": _grow,
    "health": _health,
}
