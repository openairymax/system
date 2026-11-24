# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 事件台账命令行
#
# 台账只提供记录与状态推进，处置动作由宏控与安全模块执行，命令行
# 负责把「谁在什么时候做了什么」写完整。所有子命令的出口码统一为
# 0 成功、2 参数错误、3 台账错误、4 状态机拒绝，脚本据此分流。

"""Incident ledger command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import feed, runbook
from .model import (ALLOWED, NOTE_MAX, BadInput, BadState, BadStore, State,
                    iso, now_ns, parse_iso, text_of)
from .store import Store

ROOT = "/var/lib/agentrt/incidents"
ALERTS = "/var/lib/agentrt/alerts"
ACTOR = "operator"

OK = 0
ERR_INPUT = 2
ERR_STORE = 3
ERR_STATE = 4

MOVES = {
    "triage": State.TRIAGED,
    "respond": State.RESPONDING,
    "recover": State.RECOVERING,
    "resolve": State.RESOLVED,
    "reopen": State.REOPENED,
    "escalate": State.ESCALATED,
}


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _stamp(text: str) -> int:
    try:
        return parse_iso(text)
    except BadInput as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _at(args: argparse.Namespace) -> int:
    return getattr(args, "at", 0) or now_ns()


def _who(args: argparse.Namespace) -> str:
    return (getattr(args, "actor", "") or os.environ.get("AIRY_ACTOR")
            or ACTOR)


def _emit(args: argparse.Namespace, inc: Any, text: str) -> int:
    if getattr(args, "json", False):
        print(_dump(inc.to_json()))
    else:
        print(text)
    return OK


def _common(parser: argparse.ArgumentParser, at: bool = False,
            who: bool = False, js: bool = False) -> None:
    if at:
        parser.add_argument("--at", type=_stamp,
                            help="ISO 时间戳，缺省为当前时间")
    if who:
        parser.add_argument("--actor", help="操作者，缺省取 AIRY_ACTOR")
    if js:
        parser.add_argument("--json", action="store_true", help="输出 JSON")


def cmd_create(args: argparse.Namespace, store: Store) -> int:
    inc = store.create(
        _at(args), args.severity, args.category, args.title, _who(args),
        source=args.source or "", detail=args.detail or "",
        alerts=args.alert or [], agents=args.agent or [],
        daemons=args.daemon or [],
    )
    return _emit(args, inc, f"{inc.incident_id} {inc.severity.value}"
                          f" {inc.category.value} {inc.status.value}")


def cmd_ingest(args: argparse.Namespace, store: Store) -> int:
    rows = feed.read_active(args.alerts_root)
    made: list[str] = []
    for row in rows:
        plan = feed.draft(row)
        if plan is None or store.find_alert(plan["alerts"][0]) is not None:
            continue
        inc = store.create(
            _at(args), plan["severity"], plan["category"], plan["title"],
            _who(args), source=plan["source"], detail=plan["detail"],
            alerts=plan["alerts"], agents=plan["agents"],
            daemons=plan["daemons"],
        )
        made.append(inc.incident_id)
    if getattr(args, "json", False):
        print(_dump({"created": made, "skipped": len(rows) - len(made)}))
    else:
        print(f"新建 {len(made)} 条：{' '.join(made)}" if made else "无新告警")
    return OK


def cmd_list(args: argparse.Namespace, store: Store) -> int:
    rows = store.list(args.state, args.severity, args.limit)
    if getattr(args, "json", False):
        print(_dump([inc.to_json() for inc in rows]))
        return OK
    print("ID\tSEV\tSTATE\tDETECTED\tTITLE")
    for inc in rows:
        print(f"{inc.incident_id}\t{inc.severity.value}\t"
              f"{inc.status.value}\t{iso(inc.detected_at)}\t{inc.title}")
    return OK


def cmd_show(args: argparse.Namespace, store: Store) -> int:
    inc = store.load(args.id)
    if getattr(args, "json", False):
        print(_dump(inc.to_json()))
        return OK
    print(f"编号\t{inc.incident_id}")
    print(f"级别\t{inc.severity.value}")
    print(f"类别\t{inc.category.value}")
    print(f"状态\t{inc.status.value}")
    print(f"标题\t{inc.title}")
    print(f"检测\t{iso(inc.detected_at)}")
    print(f"恢复\t{iso(inc.resolved_at) if inc.resolved_at else '未恢复'}")
    print(f"来源\t{inc.source or '未记录'}")
    print(f"根因\t{inc.root_cause or '未记录'}")
    print(f"告警\t{' '.join(inc.alerts) or '无'}")
    print(f"Agent\t{' '.join(str(i) for i in inc.affected_agents) or '无'}")
    print(f"daemon\t{' '.join(inc.affected_daemons) or '无'}")
    print(f"复盘\t{inc.postmortem.get('report_id') if inc.postmortem else '无'}")
    print("时间线")
    for row in inc.timeline:
        print(f"  {row.get('ts')} {row.get('event')} {row.get('actor')}"
              f" {row.get('detail') or ''}".rstrip())
    return OK


def cmd_move(args: argparse.Namespace, store: Store) -> int:
    inc = store.load(args.id)
    cause = (getattr(args, "root_cause", "") or "").strip()
    if cause:
        inc.root_cause = text_of(cause, "root_cause", NOTE_MAX)
    inc.switch(MOVES[args.move], _who(args), _at(args), args.detail or "")
    store.save(inc)
    return _emit(args, inc, f"{inc.incident_id} {inc.status.value}")


def cmd_act(args: argparse.Namespace, store: Store) -> int:
    inc = store.load(args.id)
    inc.action(args.name, _who(args), _at(args), args.result,
               args.detail or "")
    store.save(inc)
    return _emit(args, inc, f"{inc.incident_id} {args.name} {args.result}")


def cmd_memo(args: argparse.Namespace, store: Store) -> int:
    inc = store.load(args.id)
    if State.POSTMORTEM not in ALLOWED[inc.status]:
        raise BadState(f"{inc.incident_id}: {inc.status.value} 不能进入复盘")
    stamp = _at(args)
    root = (args.root_cause or "").strip()
    if root:
        inc.root_cause = text_of(root, "root_cause", NOTE_MAX)
    body = runbook.render(inc, _who(args), stamp, args.summary or "", root)
    path = store.report(inc, body)
    inc.postmortem = {
        "report_id": runbook.report_id(inc),
        "by": _who(args),
        "at": iso(stamp),
        "file": path.name,
    }
    inc.switch(State.POSTMORTEM, _who(args), stamp, f"report {path.name}")
    store.save(inc)
    return _emit(args, inc, f"{inc.incident_id} POSTMORTEM {path.name}")


def cmd_stats(args: argparse.Namespace, store: Store) -> int:
    stamp = _at(args)
    text = iso(stamp)
    month = args.month or text[0:4] + text[5:7]
    doc = store.stats(month, stamp)
    if getattr(args, "json", False):
        print(_dump(doc))
        return OK
    mttr = "无" if doc["mttr_s"] is None else f"{doc['mttr_s']}s"
    print(f"{doc['month']} 事件 {doc['total']} 条：未结 {doc['open']}，"
          f"已结 {doc['closed']}，MTTR {mttr}，处置动作 {doc['actions']}")
    for key in ("by_state", "by_severity"):
        body = " ".join(f"{name}={count}"
                        for name, count in sorted(doc[key].items()))
        print(f"{key}: {body}")
    return OK


def cmd_prune(args: argparse.Namespace, store: Store) -> int:
    picked, moved = store.prune(args.days, _at(args), args.apply,
                                args.sec_days)
    if getattr(args, "json", False):
        print(_dump({"candidates": [inc.incident_id for inc in picked],
                     "moved": moved}))
    elif args.apply:
        print(f"已归档 {len(moved)} 条：{' '.join(moved)}")
    else:
        print(f"候选 {len(picked)} 条："
              f"{' '.join(inc.incident_id for inc in picked)}")
    return OK


def cmd_books(args: argparse.Namespace, store: Store) -> int:
    for name in runbook.names():
        print(name)
    return OK


def cmd_book(args: argparse.Namespace, store: Store) -> int:
    print(runbook.text(args.name), end="")
    return OK


def _parser() -> argparse.ArgumentParser:
    root = os.environ.get("AIRY_INCIDENT_ROOT", ROOT)
    parser = argparse.ArgumentParser(
        prog="airy-incident",
        description="AirymaxOS 事件台账命令行",
    )
    parser.add_argument("--root", default=root, help="台账根目录")
    sub = parser.add_subparsers(dest="cmd", required=True)

    one = sub.add_parser("create", help="登记一条事件")
    one.add_argument("--severity", required=True, help="P0/P1/P2/P3")
    one.add_argument("--category", required=True,
                     help="system/agent/security/network/resource")
    one.add_argument("--title", required=True, help="一句话描述")
    one.add_argument("--source", help="检测来源")
    one.add_argument("--detail", help="检测细节")
    one.add_argument("--alert", action="append", help="关联告警编号")
    one.add_argument("--agent", action="append", type=int, help="受影响 Agent")
    one.add_argument("--daemon", action="append", help="受影响 daemon")
    _common(one, at=True, who=True, js=True)
    one.set_defaults(func=cmd_create)

    two = sub.add_parser("ingest", help="从活动告警建单")
    two.add_argument("--alerts-root", default=ALERTS, help="告警根目录")
    _common(two, at=True, who=True, js=True)
    two.set_defaults(func=cmd_ingest)

    three = sub.add_parser("list", help="列出事件")
    three.add_argument("--state", help="按状态过滤")
    three.add_argument("--severity", help="按级别过滤")
    three.add_argument("--limit", type=int, default=200, help="最多返回条数")
    _common(three, js=True)
    three.set_defaults(func=cmd_list)

    four = sub.add_parser("show", help="查看一条事件")
    four.add_argument("id")
    _common(four, js=True)
    four.set_defaults(func=cmd_show)

    for name, target in MOVES.items():
        move = sub.add_parser(name, help=f"推进到 {target.value}")
        move.add_argument("id")
        move.add_argument("--detail", help="本次迁移备注")
        if target is State.RESOLVED:
            move.add_argument("--root-cause", help="根因结论")
        _common(move, at=True, who=True, js=True)
        move.set_defaults(func=cmd_move, move=name)

    act = sub.add_parser("act", help="登记一次处置动作")
    act.add_argument("id")
    act.add_argument("--name", required=True, help="动作名")
    act.add_argument("--result", default="success",
                     choices=("success", "failed"))
    act.add_argument("--detail", help="动作细节")
    _common(act, at=True, who=True, js=True)
    act.set_defaults(func=cmd_act)

    memo = sub.add_parser("postmortem", help="生成复盘报告并归档")
    memo.add_argument("id")
    memo.add_argument("--summary", help="事件摘要")
    memo.add_argument("--root-cause", help="根因结论")
    memo.add_argument("--by", dest="actor", help="报告人")
    _common(memo, at=True, js=True)
    memo.set_defaults(func=cmd_memo)

    stat = sub.add_parser("stats", help="统计事件")
    stat.add_argument("--month", help="YYYYMM，缺省为当月")
    _common(stat, at=True, js=True)
    stat.set_defaults(func=cmd_stats)

    prune = sub.add_parser("prune", help="按保留期归档")
    prune.add_argument("--days", type=int, default=90, help="常规保留天数")
    prune.add_argument("--sec-days", type=int, default=365,
                       help="安全事件保留天数")
    prune.add_argument("--apply", action="store_true", help="实际执行归档")
    _common(prune, at=True, js=True)
    prune.set_defaults(func=cmd_prune)

    books = sub.add_parser("runbooks", help="列出运行手册")
    books.set_defaults(func=cmd_books)

    book = sub.add_parser("runbook", help="打印一份运行手册")
    book.add_argument("name")
    book.set_defaults(func=cmd_book)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.func(args, Store(args.root))
    except BadStore as exc:
        print(f"airy-incident: {exc}", file=sys.stderr)
        return ERR_STORE
    except BadState as exc:
        print(f"airy-incident: {exc}", file=sys.stderr)
        return ERR_STATE
    except BadInput as exc:
        print(f"airy-incident: {exc}", file=sys.stderr)
        return ERR_INPUT
    except OSError as exc:
        print(f"airy-incident: {exc}", file=sys.stderr)
        return ERR_STORE


if __name__ == "__main__":
    sys.exit(main())
