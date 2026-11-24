# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 告警命令行
#
# 查询走状态文件与归档，写入走守护进程的 Unix 套接字。两类操作分开，运维
# 在守护进程不可用时仍能读到上一份状态；写入失败按「未送达」返回非零，不
# 假装成功。所有输出都是 JSON 行，便于 devstation 与脚本直接消费。

"""airy-alert command line."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path
from typing import Any

from .model import BadEvent, NS, SOURCES
from .rules import RuleError, load_rules
from .server import DEFAULT_RULES, DEFAULT_SOCKET, DEFAULT_STATE
from .store import Store

E_OK = 0
E_USE = 2
E_MISS = 3
E_REJECT = 4
HIST_MAX = 10000


class Remote(BadEvent):
    """守护进程受理了请求但拒绝其内容。"""


def _store(args: argparse.Namespace) -> Store:
    return Store(args.state)


def _dump(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
          flush=True)


def _request(sock_path: str, obj: dict[str, Any]) -> dict[str, Any]:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(sock_path)
            sock.sendall(json.dumps(obj, ensure_ascii=False).encode() + b"\n")
            with sock.makefile("rb") as handle:
                line = handle.readline()
    except OSError as exc:
        raise BadEvent(f"daemon unreachable: {exc}") from exc
    if not line:
        raise BadEvent("daemon closed the connection")
    try:
        reply = json.loads(line)
    except json.JSONDecodeError as exc:
        raise BadEvent(f"bad reply: {exc}") from exc
    if not isinstance(reply, dict):
        raise BadEvent("reply must be an object")
    if "error" in reply:
        raise Remote(str(reply["error"]))
    return reply


def _since(text: str) -> int:
    units = {"s": NS, "m": 60 * NS, "h": 3600 * NS, "d": 86400 * NS}
    if not text:
        return 0
    if text[-1] not in units or not text[:-1].isdigit():
        raise BadEvent(f"bad since: {text!r} (use 30s/10m/2h/1d)")
    return time.time_ns() - int(text[:-1]) * units[text[-1]]


def _labels(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in pairs:
        key, sep, val = item.partition("=")
        if not sep or not key:
            raise BadEvent(f"bad label: {item!r} (use k=v)")
        out[key] = val
    return out


def cmd_status(args: argparse.Namespace) -> int:
    try:
        doc = _store(args).status()
    except ValueError as exc:
        _dump({"error": str(exc)})
        return E_MISS
    if not doc:
        _dump({"error": "no status file"})
        return E_MISS
    _dump(doc)
    return E_OK


def cmd_list(args: argparse.Namespace) -> int:
    try:
        rows = _store(args).load()
    except ValueError as exc:
        _dump({"error": str(exc)})
        return E_MISS
    for row in rows:
        _dump(row)
    return E_OK


def cmd_history(args: argparse.Namespace) -> int:
    if not 1 <= args.limit <= HIST_MAX:
        _dump({"error": f"limit must be 1..{HIST_MAX}"})
        return E_USE
    store = _store(args)
    try:
        since = _since(args.since)
        rows = store.history(
            severity=args.severity,
            source=args.source,
            agent=args.agent,
            code=args.code,
            since_ns=since,
            limit=args.limit,
        )
    except (BadEvent, ValueError) as exc:
        _dump({"error": str(exc)})
        return E_USE
    for row in rows:
        _dump(row)
    return E_OK


def cmd_ack(args: argparse.Namespace) -> int:
    try:
        reply = _request(args.socket, {
            "kind": "ack",
            "alert_id": args.alert_id,
            "operator": args.operator,
            "comment": args.comment,
        })
    except Remote as exc:
        _dump({"error": str(exc)})
        return E_REJECT
    except BadEvent as exc:
        _dump({"error": str(exc)})
        return E_MISS
    _dump(reply)
    return E_OK if reply.get("action") == "ACK" else E_REJECT


def _event(args: argparse.Namespace) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "kind": args.kind,
        "severity": args.severity,
        "source": args.source,
        "code": args.code,
        "agent": args.agent,
        "message": args.message,
        "labels": _labels(args.label),
    }
    if args.ts_ns is not None:
        obj["ts_ns"] = args.ts_ns
    return obj


def cmd_send(args: argparse.Namespace) -> int:
    try:
        reply = _request(args.socket, _event(args))
    except Remote as exc:
        _dump({"error": str(exc)})
        return E_REJECT
    except BadEvent as exc:
        _dump({"error": str(exc)})
        return E_MISS
    _dump(reply)
    return E_OK


def cmd_rules(args: argparse.Namespace) -> int:
    try:
        rules = load_rules(args.rules or DEFAULT_RULES)
    except RuleError as exc:
        _dump({"error": str(exc)})
        return E_USE
    _dump({"rules": len(rules), "names": [rule.name for rule in rules]})
    return E_OK


def cmd_sources(_args: argparse.Namespace) -> int:
    for sid, name in SOURCES.items():
        _dump({"source": name, "id": sid})
    return E_OK


def _common_state(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state", default=DEFAULT_STATE)


def _common_sock(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--socket", default=DEFAULT_SOCKET)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airy-alert", description="AirymaxOS alert client")
    sub = parser.add_subparsers(dest="cmd", required=True)

    status = sub.add_parser("status", help="show daemon status")
    _common_state(status)
    status.set_defaults(func=cmd_status)

    listing = sub.add_parser("list", help="list active alerts")
    _common_state(listing)
    listing.set_defaults(func=cmd_list)

    history = sub.add_parser("history", help="query archived alerts")
    _common_state(history)
    history.add_argument("--severity", choices=("INFO", "WARNING", "CRITICAL"))
    history.add_argument("--source", choices=tuple(SOURCES.values()))
    history.add_argument("--agent", type=int)
    history.add_argument("--code", type=int)
    history.add_argument("--since", default="")
    history.add_argument("--limit", type=int, default=1000)
    history.set_defaults(func=cmd_history)

    ack = sub.add_parser("ack", help="confirm one alert")
    _common_sock(ack)
    ack.add_argument("alert_id")
    ack.add_argument("--operator", default="operator")
    ack.add_argument("--comment", default="")
    ack.set_defaults(func=cmd_ack)

    for name, wire in (("send", "alert"), ("recover", "recover")):
        send = sub.add_parser(name, help=f"{name} one alert record")
        _common_sock(send)
        send.add_argument("--severity", required=True,
                          choices=("INFO", "WARNING", "CRITICAL"))
        send.add_argument("--source", default="macro_d", choices=tuple(SOURCES.values()))
        send.add_argument("--code", type=int, default=0)
        send.add_argument("--agent", type=int, default=0)
        send.add_argument("--message", default="")
        send.add_argument("--label", action="append", default=[])
        send.add_argument("--ts-ns", type=int, default=None)
        send.set_defaults(func=cmd_send, kind=wire)

    rules = sub.add_parser("rules", help="validate suppression rules")
    rules.add_argument("--rules", action="append", default=None)
    rules.set_defaults(func=cmd_rules)

    sources = sub.add_parser("sources", help="print source id table")
    sources.set_defaults(func=cmd_sources)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except BrokenPipeError:
        return E_OK


if __name__ == "__main__":
    sys.exit(main())
