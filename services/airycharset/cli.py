# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""airy-charset 命令行。

show/count 只读；check 判定存量文件是否为合法 UTF-8；convert 把允许入站的
外源编码转成 UTF-8。退出码分层见 airylib.err：4 表示机器缺东西，5 表示数据
或配置本身不合规，6 表示写出失败。分层保留的意义在于日志能分辨「环境」与
「策略」，不要把两者压成同一个失败。
"""

from __future__ import annotations

import argparse
import codecs
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from airylib.cli import boot, emit
from airylib.err import (EXIT_ENV, EXIT_OK, EXIT_POLICY, EXIT_RUN, EXIT_USAGE,
                         Fault)

from . import conf, scan

__all__ = ["EXIT_ENV", "EXIT_OK", "EXIT_POLICY", "EXIT_RUN", "EXIT_USAGE",
           "main"]

PROG = "airy-charset"
CONF = "/etc/agentrt/charset.conf"


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行并分派；返回值即进程退出码。"""
    return boot(PROG, argv, _args)


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=PROG)
    subs = parser.add_subparsers(dest="cmd", required=True)
    _sub_show(subs)
    _sub_check(subs)
    _sub_count(subs)
    _sub_convert(subs)
    return parser.parse_args(argv)


def _sub_show(subs: argparse._SubParsersAction) -> None:
    one = subs.add_parser("show", help="显示编码策略")
    one.add_argument("--conf", default=CONF)
    one.add_argument("--json", action="store_true")
    one.set_defaults(func=cmd_show)


def _sub_check(subs: argparse._SubParsersAction) -> None:
    one = subs.add_parser("check", help="判定文件是否为合法 UTF-8")
    one.add_argument("files", nargs="+")
    one.add_argument("--conf", default=CONF)
    one.add_argument("--json", action="store_true")
    one.set_defaults(func=cmd_check)


def _sub_count(subs: argparse._SubParsersAction) -> None:
    one = subs.add_parser("count", help="按码点计数")
    one.add_argument("files", nargs="*")
    one.add_argument("--text")
    one.add_argument("--conf", default=CONF)
    one.add_argument("--json", action="store_true")
    one.set_defaults(func=cmd_count)


def _sub_convert(subs: argparse._SubParsersAction) -> None:
    one = subs.add_parser("convert", help="把外源编码转成 UTF-8")
    one.add_argument("--from", dest="src", required=True)
    one.add_argument("--in", dest="inp")
    one.add_argument("--out")
    one.add_argument("--conf", default=CONF)
    one.set_defaults(func=cmd_convert)


def _policy(path: str) -> conf.Settings | None:
    """读取并校验策略；缺省路径不存在时按「无覆盖」处理。"""
    if not Path(path).exists():
        return None
    raw = conf.read(path)
    bad = conf.problems(raw)
    if bad:
        raise Fault(f"{path}: {bad[0]}", EXIT_POLICY)
    return conf.settings(raw)


def _must(path: str) -> conf.Settings:
    """显式指定的策略必须存在：缺文件是环境问题，不是策略问题。"""
    cfg = _policy(path)
    if cfg is None:
        raise Fault(f"缺少配置文件: {path}", EXIT_ENV)
    return cfg


def _read(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise Fault(f"无法读取 {path}: {exc.strerror}", EXIT_ENV) from exc


def cmd_show(args: argparse.Namespace) -> int:
    """显示策略；缺省配置缺失是环境问题，不是策略问题。"""
    cfg = _must(args.conf)
    if args.json:
        print(json.dumps({
            "enc": cfg.enc,
            "sources": list(cfg.sources),
            "bom": cfg.bom,
            "nonchar": cfg.nonchar,
            "count": cfg.count,
        }, ensure_ascii=False, sort_keys=True))
        return EXIT_OK
    print(f"CHARSET_ENC={cfg.enc}")
    print(f"CHARSET_SOURCES={','.join(cfg.sources)}")
    print(f"CHARSET_BOM={cfg.bom}")
    print(f"CHARSET_NONCHAR={cfg.nonchar}")
    print(f"CHARSET_COUNT={cfg.count}")
    print(f"AIRYMAX-CHARSET-SHOW ok enc={cfg.enc} sources={len(cfg.sources)}")
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    """逐个文件判定；坏文件定位到字节偏移，输出与退出码由 emit 统一。"""
    cfg = _policy(args.conf)
    rows = _judge(args.files)
    issues = _issues(rows)
    total = sum(row["cp"] for row in rows)
    enc = cfg.enc if cfg else "UTF-8"
    body = {"ok": not issues, "files": rows, "enc": enc}
    return emit(PROG, issues, f"AIRYMAX-CHARSET-CHECK ok files={len(rows)} "
                f"cp={total}", args.json, body)


def _judge(files: Sequence[str]) -> list[dict]:
    rows = []
    for name in files:
        cp, issue = scan.measure(_read(name))
        rows.append({"path": name, "cp": cp,
                     "issue": None if issue is None else issue.text(name)})
    return rows


def _issues(rows: list[dict]) -> list[str]:
    return [row["issue"] for row in rows if row["issue"]]


def cmd_count(args: argparse.Namespace) -> int:
    """按码点计数；Token 预算与截断预算都以此为准。"""
    if not args.files and args.text is None:
        raise Fault("至少给一个文件或 --text", EXIT_USAGE)
    _policy(args.conf)
    rows: list[dict] = []
    if args.text is not None:
        _count_one("--text", args.text.encode("utf-8"), rows)
    for name in args.files:
        _count_one(name, _read(name), rows)
    total = sum(row["cp"] for row in rows)
    body = {"ok": True, "files": rows, "total": total}
    return emit(PROG, [], f"AIRYMAX-CHARSET-COUNT ok files={len(rows)} "
                f"cp={total}", args.json, body)


def _count_one(name: str, data: bytes, rows: list[dict]) -> None:
    cp, issue = scan.measure(data)
    if issue is not None:
        raise Fault(f"{name} 不是合法 UTF-8: {issue.text(name)}", EXIT_POLICY)
    rows.append({"path": name, "cp": cp})


def cmd_convert(args: argparse.Namespace) -> int:
    """把外源编码转成 UTF-8；只写目标字节，不假设调用方已做校验。"""
    cfg = _policy(args.conf)
    if cfg is not None and args.src.lower() not in [
            name.lower() for name in cfg.sources]:
        raise Fault(f"编码不在允许入站清单: {args.src}", EXIT_POLICY)
    _codec(args.src)
    raw = sys.stdin.buffer.read() if args.inp is None else _read(args.inp)
    out = _decode(raw, args.src).encode("utf-8")
    cp, issue = scan.measure(out)
    if issue is not None:
        raise Fault(issue.text(args.src), EXIT_POLICY)
    _write(args.out, out)
    print(f"AIRYMAX-CHARSET-CONVERT ok from={args.src} "
          f"bytes={len(out)} cp={cp}", file=sys.stderr)
    return EXIT_OK


def _codec(name: str) -> None:
    try:
        codecs.lookup(name)
    except LookupError as exc:
        raise Fault(f"未知编码: {name}", EXIT_POLICY) from exc


def _decode(raw: bytes, name: str) -> str:
    try:
        return raw.decode(name, errors="strict")
    except UnicodeDecodeError as exc:
        raise Fault(f"{name} 解码失败: {exc}", EXIT_POLICY) from exc


def _write(path: str | None, data: bytes) -> None:
    if path is None:
        sys.stdout.buffer.write(data)
        return
    try:
        Path(path).write_bytes(data)
    except OSError as exc:
        raise Fault(f"无法写出 {path}: {exc.strerror}", EXIT_RUN) from exc
