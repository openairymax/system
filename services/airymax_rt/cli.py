# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 实时能力命令行入口
#
# 子命令对应三张契约表：status/apply/config 管实时带宽，matrix/coexist 管
# 能力探测与桌面共存，memcap 补上 systemd 在 v1 层级上不写的那一半内存
# 天花板，admit 在启动前决定新实例装不装得下。启动单元与验收门禁共用同一
# 份判定实现，避免「启动时写成功、门禁读失败」的两套口径；能力矩阵同理，
# 探测与报告只此一处。
#
# 退出码：0 表示已全部落地、内核不限预算或整机满足完整实时；3 表示部分
# 落地或仅有软实时；1 表示未通过且没有任何 slice 落地或完全不具备实时；
# 2 为用法错误。门禁据此直接判读，不必解析人类可读输出。

"""AirymaxOS RT command line (X-01, X-03).

设计依据：0.0.4 方案 §6.7 X-01/X-03 与 docs-linux/100-operations/
02-configuration.md §2.2、docs-linux/170-performance/07-rt-matrix.md。
``--json`` 输出与 :meth:`Report.dump` / :meth:`Matrix.dump` 同形，二者
是同一份数据的两种呈现。

"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from . import admit, bandwidth, coexist, config, defaults, matrix, memcap
from .policy import (
    DEFAULT_CGROUP,
    DEFAULT_PERIOD,
    DEFAULT_TOTAL,
    MARK,
    Policy,
)
from .report import Report, State

__all__ = ["EXIT_FAIL", "EXIT_PARTIAL", "EXIT_USAGE", "main"]

EXIT_FAIL = 1

EXIT_USAGE = 2

EXIT_PARTIAL = 3

PASSING = (State.OK, State.DISABLED)


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行并分派；返回值即进程退出码。"""
    args = _args(argv)
    if args.cmd == "matrix":
        return _matrix(args)
    if args.cmd == "coexist":
        return _coexist(args)
    if args.cmd == "memcap":
        return _memcap(args)
    if args.cmd == "admit":
        return _admit(args)
    if args.cmd == "defaults":
        return _defaults(args)
    if args.cmd == "seed":
        return _seed(args)
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
    opt.add_argument(
        "--sysroot",
        metavar="DIR",
        default=argparse.SUPPRESS,
        help="sysfs 根（能力探测）",
    )
    opt.add_argument(
        "--procroot",
        metavar="DIR",
        default=argparse.SUPPRESS,
        help="procfs 根（能力探测）",
    )
    opt.add_argument(
        "--cmdline",
        metavar="FILE",
        default=argparse.SUPPRESS,
        help="内核命令行文件",
    )
    opt.add_argument(
        "--systemctl",
        metavar="FILE",
        default=argparse.SUPPRESS,
        help="systemctl 路径（共存策略）",
    )
    opt.add_argument(
        "--desktop",
        metavar="UNIT",
        default=argparse.SUPPRESS,
        help="桌面 slice 单元名（共存策略）",
    )
    opt.add_argument(
        "--pressure",
        metavar="FILE",
        default=argparse.SUPPRESS,
        help="PSI CPU 计数文件（共存策略）",
    )
    opt.add_argument(
        "--loadavg",
        metavar="FILE",
        default=argparse.SUPPRESS,
        help="负载均值文件（共存策略回退信号）",
    )
    opt.add_argument(
        "--apply",
        action="store_true",
        default=argparse.SUPPRESS,
        help="落地运行时权重或内存上限并回读",
    )
    opt.add_argument(
        "--unitdir",
        metavar="DIR",
        default=argparse.SUPPRESS,
        help="单元目录（内存上限的声明来源）",
    )
    opt.add_argument(
        "--need",
        metavar="SIZE",
        default=argparse.SUPPRESS,
        help="新实例预计占用（准入判定）",
    )
    opt.add_argument(
        "--meminfo",
        metavar="FILE",
        default=argparse.SUPPRESS,
        help="meminfo 路径（准入判定的内存来源）",
    )
    opt.add_argument(
        "--group",
        action="append",
        metavar="NAME",
        default=argparse.SUPPRESS,
        help="被测或待补写的 cgroup 组名（可重复）",
    )
    opt.add_argument(
        "--subtree",
        metavar="NAME",
        default=argparse.SUPPRESS,
        help="补写该组下已存在的子树",
    )
    opt.add_argument(
        "--depth",
        type=int,
        metavar="N",
        default=argparse.SUPPRESS,
        help="子树展开层数，默认 1",
    )
    opt.add_argument(
        "--limits",
        metavar="FILE",
        default=argparse.SUPPRESS,
        help="RLIMIT_RTPRIO 来源（默认 /proc/self/limits）",
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
        sysroot=matrix.DEFAULT_SYS,
        procroot=matrix.DEFAULT_PROC,
        cmdline=matrix.DEFAULT_CMDLINE,
        systemctl=coexist.DEFAULT_SYSTEMCTL,
        desktop=coexist.DEFAULT_DESKTOP,
        pressure=coexist.DEFAULT_PRESSURE,
        loadavg=coexist.DEFAULT_LOADAVG,
        unitdir=memcap.DEFAULT_UNITDIR,
        need="",
        meminfo=admit.DEFAULT_MEMINFO,
        group=[],
        subtree="",
        depth=1,
        limits=defaults.DEFAULT_LIMITS,
        apply=False,
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
    sub.add_parser(
        "matrix", parents=[_opt()], help="探测实时能力并汇总等级"
    )
    sub.add_parser(
        "coexist", parents=[_opt()], help="评估桌面与实时共存策略"
    )
    sub.add_parser(
        "memcap", parents=[_opt()], help="补齐内存与换页上限"
    )
    sub.add_parser(
        "admit", parents=[_opt()], help="判定新实例能否装入"
    )
    sub.add_parser(
        "defaults", parents=[_opt()], help="判读新建组的带宽默认值"
    )
    sub.add_parser(
        "seed", parents=[_opt()], help="为新组补写有限预算"
    )
    return parser.parse_args(argv)


def _matrix(args: argparse.Namespace) -> int:
    """探测实时能力；等级决定退出码，与带宽判定分开。"""
    found = matrix.collect(_roots(args))
    _show(found, args.json)
    return _mcode(found.grade)


def _coexist(args: argparse.Namespace) -> int:
    """评估共存策略；applied 为假表示运行时权重与目标不一致。"""
    cfg = coexist.Coexist(
        unit=args.desktop,
        systemctl=args.systemctl,
        pressure=args.pressure,
        loadavg=args.loadavg,
        roots=_roots(args),
    )
    found = coexist.apply(cfg) if args.apply else coexist.inspect(cfg)
    _show_plan(found, args.json)
    return _ccode(found)


def _memcap(args: argparse.Namespace) -> int:
    """补齐或核对内存与换页上限；声明仍只读自单元文件。"""
    if args.apply:
        found = memcap.apply(args.cgroup, args.unitdir)
    else:
        found = memcap.inspect(args.cgroup, args.unitdir)
    _show_mem(found, args.json)
    return _memcode(found)


def _admit(args: argparse.Namespace) -> int:
    """准入只判不驱逐：拒绝时给出缺口，已在跑的实例一个都不动。"""
    need = admit.size(args.need)
    if need <= 0:
        sys.stderr.write("airy-rt-cap: 需要 --need SIZE，例如 --need 12G\n")
        return EXIT_USAGE
    found = admit.check(need, args.meminfo, args.unitdir)
    _show_admit(found, args.json)
    return 0 if found.state == admit.FIT else EXIT_PARTIAL


def _defaults(args: argparse.Namespace) -> int:
    """判读新建组的三道门禁；blocked 可按 seed 补齐，故为部分落地。"""
    group = args.group[0] if args.group else ""
    found = defaults.inspect(_policy(args), group, args.limits)
    _show_defaults(found, args.json)
    if found.usable:
        return 0
    return EXIT_FAIL if found.verdict == defaults.UNKNOWN else EXIT_PARTIAL


def _seed(args: argparse.Namespace) -> int:
    """补写新组预算；一次覆盖 --subtree 与 --group 两处来源。"""
    if not args.group and not args.subtree:
        sys.stderr.write(
            "airy-rt-cap: 需要 --group NAME 或 --subtree NAME\n"
        )
        return EXIT_USAGE
    policy = _policy(args)
    steps = ()
    if args.subtree:
        steps = defaults.seed_subtree(policy, args.subtree, args.depth)
    if args.group:
        steps += defaults.seed(policy, tuple(args.group))
    _show_seed(steps, args.json)
    return _seedcode(steps)


def _roots(args: argparse.Namespace) -> matrix.Roots:
    return matrix.Roots(
        sys=args.sysroot,
        proc=args.procroot,
        cgroup=args.cgroup,
        cmdline=args.cmdline,
    )


def _show(found: matrix.Matrix, as_json: bool) -> None:
    if as_json:
        print(json.dumps(found.dump(), ensure_ascii=False))
        return
    print(f"{MARK} grade={found.grade.value} caps={len(found.caps)}")
    for item in found.caps:
        print(f"  {item.name:<12}{item.flag.value:<8} {item.detail}")
    print(f"advice {matrix.advice(found.grade)}")


def _mcode(found: matrix.Grade) -> int:
    if found is matrix.Grade.READY:
        return 0
    if found is matrix.Grade.SOFT:
        return EXIT_PARTIAL
    return EXIT_FAIL


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


def _show_plan(found: coexist.Plan, as_json: bool) -> None:
    if as_json:
        print(json.dumps(found.dump(), ensure_ascii=False))
        return
    risk = "-" if found.pressure is None else f"{found.pressure:.1f}%"
    print(
        f"{MARK} mode={found.mode.value} unit={found.unit}"
        f" weight={found.weight} current={found.current}"
        f" applied={found.applied}"
    )
    print(
        f"grade {found.grade.value} admit={found.admit}"
        f" pressure={risk} origin={found.origin}"
    )
    for note in found.notes:
        print(f"note   {note}")
    if found.detail:
        print(f"detail {found.detail}")
    print(f"advice {coexist.advice(found.mode)}")


def _ccode(found: coexist.Plan) -> int:
    if found.mode is coexist.Mode.FAILED:
        return EXIT_FAIL
    if found.mode is coexist.Mode.ABSENT or found.applied:
        return 0
    return EXIT_PARTIAL


def _show_mem(found: memcap.Report, as_json: bool) -> None:
    if as_json:
        print(json.dumps(found.dump(), ensure_ascii=False))
        return
    print(f"{MARK} layout={found.layout or '-'} steps={len(found.steps)}")
    for item in found.steps:
        have = "-" if item.have is None else item.have
        print(
            f"  {item.path:<38}{item.want:>14}  {have:>14}"
            f"  {item.state}"
        )
        if item.note:
            print(f"note   {item.path} {item.note}")


def _show_admit(found: admit.Verdict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(found.dump(), ensure_ascii=False))
        return
    print(
        f"{MARK} admit={found.state} need={found.need}"
        f" free={found.free} short={found.short}"
    )
    print(
        f"total  {found.total}  avail {found.avail}"
        f"  reserve {found.keep}  swap {found.swap}"
    )
    if found.note:
        print(f"note   {found.note}")
    print(f"advice {admit.advice(found.state)}")


def _memcode(found: memcap.Report) -> int:
    """记账树缺失按未通过，任何一条没落地按部分落地。"""
    if not found.layout:
        return EXIT_FAIL
    failed = (
        memcap.ABSENT,
        memcap.DIFFERS,
        memcap.TIGHT,
        memcap.HELD,
    )
    if not found.steps or any(found.count(state) for state in failed):
        return EXIT_PARTIAL
    return 0


def _show_defaults(found: defaults.Defaults, as_json: bool) -> None:
    if as_json:
        print(json.dumps(found.dump(), ensure_ascii=False))
        return
    kind = found.kind.value if found.kind else "-"
    print(
        f"{MARK} verdict={found.verdict} kind={kind}"
        f" enabled={found.enabled}"
    )
    print(
        f"total  {_us(found.total)}  period {_us(found.period)}"
        f"  share root {found.root or '-'}"
    )
    print(
        f"group  {found.group or '-'}  fresh {found.fresh or '-'}"
        f"  rtprio {_us(found.rtprio)}"
    )
    if found.blocked:
        print("blocked " + " ".join(found.blocked))
    print(f"advice {defaults.advice(found.verdict)}")


def _us(value: int | None) -> str:
    """预算与 rlimit 的呈现：-1 是不限，读不到是未知。"""
    if value is None:
        return "unknown"
    if value < 0:
        return "unlimited"
    return f"{value} us"


def _seedcode(steps: tuple[defaults.Seed, ...]) -> int:
    """空步长为无事可做；只要有一组落地即部分落地。"""
    if not steps:
        return 0
    done = (defaults.SEEDED, defaults.KEPT)
    states = [item.state for item in steps]
    if all(state in done for state in states):
        return 0
    if any(state in done for state in states):
        return EXIT_PARTIAL
    return EXIT_FAIL


def _show_seed(steps: tuple[defaults.Seed, ...], as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                {"steps": [item.dump() for item in steps]},
                ensure_ascii=False,
            )
        )
        return
    print(f"{MARK} steps={len(steps)}")
    for item in steps:
        have = "-" if item.have is None else item.have
        print(f"  {item.group:<38}{item.want:>10}  {have:>10}  {item.state}")
        if item.note:
            print(f"note   {item.group} {item.note}")
