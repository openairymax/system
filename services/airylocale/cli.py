# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# airy-locale 命令行
#
# list/show 只读；gen 只调用 localedef 补齐缺失项；check 把矩阵与
# locale.conf 放在同一套判定里，装机段用它作为「locale 落地」的判据。
# 退出码分层：0 成功；2 用法错误；4 环境不具备（矩阵不可读、缺 localedef）；
# 5 策略拒绝（矩阵不合法、配置项不在矩阵内、目标 locale 未登记）；
# 6 生成失败。4 与 5 分开，日志里能一眼分辨「机器缺工具」与「配置写错了」。

"""AirymaxOS locale command line."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from . import matrix

__all__ = [
    "EXIT_ENV",
    "EXIT_OK",
    "EXIT_POLICY",
    "EXIT_RUN",
    "main",
]

EXIT_OK = 0
EXIT_ENV = 4
EXIT_POLICY = 5
EXIT_RUN = 6

CONF = "/etc/locale.conf"
LOCALEDEF = "localedef"
LOCALE_DIR = "usr/lib/locale"
ROOT = "/"


class Fail(Exception):
    """带退出码的失败；命令行只在顶层转换一次。"""

    def __init__(self, code: int, msg: str) -> None:
        super().__init__(msg)
        self.code = code
        self.msg = msg


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行并分派；返回值即进程退出码。"""
    args = _args(argv)
    try:
        return args.func(args)
    except Fail as exc:
        print(f"airy-locale: {exc.msg}", file=sys.stderr)
        return exc.code


def cmd_list(args: argparse.Namespace) -> int:
    """列出矩阵与生成状态。"""
    rows = _rows(args.matrix)
    done = _have(args.root)
    if args.json:
        print(json.dumps({
            "default": matrix.DEFAULT,
            "locales": [_entry(row, done) for row in rows],
        }, ensure_ascii=False, sort_keys=True))
        return EXIT_OK
    print(f"{'PRIO':<4} {'LOCALE':<14} {'GENERATED':<9} SOURCE")
    for row in rows:
        mark = "yes" if _key(row.posix) in done else "no"
        print(f"{row.prio:<4} {row.posix:<14} {mark:<9} {row.source}")
    return EXIT_OK


def cmd_show(args: argparse.Namespace) -> int:
    """打印 locale.conf 的现有取值。"""
    path = Path(args.conf)
    if not path.is_file():
        raise Fail(EXIT_ENV, f"没有 locale 配置: {path}")
    env = matrix.read_conf(path)
    if args.json:
        print(json.dumps(env, ensure_ascii=False, sort_keys=True))
        return EXIT_OK
    for key in sorted(env):
        print(f"{key}={env[key]}")
    return EXIT_OK


def cmd_gen(args: argparse.Namespace) -> int:
    """按矩阵生成缺失的 locale。"""
    rows = _rows(args.matrix)
    targets = _targets(args, rows)
    done = _have(args.root)
    ok = skip = fail = 0
    for row in targets:
        if _key(row.posix) in done and not args.force:
            skip += 1
            print(f"skip {row.posix}")
            continue
        argv = _cmd(row, args.root)
        if args.dry_run:
            print(" ".join(argv))
            ok += 1
        elif _run(argv):
            ok += 1
        else:
            fail += 1
    print(f"AIRYMAX-LOCALE-GEN ok={ok} skip={skip} fail={fail}")
    return EXIT_OK if not fail else EXIT_RUN


def cmd_check(args: argparse.Namespace) -> int:
    """校验矩阵与已配置项；装机段以它作为落地判据。"""
    rows = _rows(args.matrix)
    bad = matrix.problems(rows)
    if not args.matrix_only:
        bad += _conf_bad(Path(args.conf or CONF), rows, args.conf is not None)
    if args.require_generated:
        done = _have(args.root)
        bad += [f"{row.posix}: 未生成" for row in rows
                if row.prio == "P0" and _key(row.posix) not in done]
    if args.json:
        print(json.dumps({"ok": not bad, "problems": bad}, ensure_ascii=False))
    elif bad:
        for item in bad:
            print(f"airy-locale: {item}", file=sys.stderr)
    else:
        print(f"AIRYMAX-LOCALE-CHECK ok locales={len(rows)} "
              f"p0={_count(rows, 'P0')}")
    return EXIT_OK if not bad else EXIT_POLICY


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="airy-locale", description="AirymaxOS locale 矩阵与生成")
    sub = ap.add_subparsers(dest="cmd", required=True)
    _common(sub, "list", "列出矩阵与生成状态").add_argument(
        "--root", default=ROOT, help="已装根目录")
    show = _common(sub, "show", "打印 locale.conf 取值")
    show.add_argument("--conf", default=CONF, help="locale 配置路径")
    gen = _common(sub, "gen", "生成缺失的 locale")
    gen.add_argument("names", nargs="*", help="矩阵内的 POSIX 名")
    gen.add_argument("--all", action="store_true", help="生成全部条目")
    gen.add_argument("--root", default=ROOT, help="输出前缀")
    gen.add_argument("--force", action="store_true", help="已存在也重建")
    gen.add_argument("--dry-run", action="store_true", help="只打印命令")
    check = _common(sub, "check", "校验矩阵与配置")
    check.add_argument("--conf", help="locale 配置路径")
    check.add_argument("--matrix-only", action="store_true", help="只校验矩阵")
    check.add_argument("--require-generated", action="store_true",
                       help="要求 P0 已生成")
    check.add_argument("--root", default=ROOT, help="已装根目录")
    ns = ap.parse_args(argv)
    ns.func = _FUNCS[ns.cmd]
    return ns


def _common(sub, name: str, text: str) -> argparse.ArgumentParser:
    p = sub.add_parser(name, help=text)
    p.add_argument("--matrix", help="矩阵文件")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    return p


_FUNCS = {
    "list": cmd_list,
    "show": cmd_show,
    "gen": cmd_gen,
    "check": cmd_check,
}


def _rows(path: str | None) -> list[matrix.Row]:
    try:
        return matrix.load(path)
    except ValueError as exc:
        raise Fail(EXIT_POLICY, str(exc)) from exc
    except OSError as exc:
        raise Fail(EXIT_ENV, f"读取矩阵失败: {exc}") from exc


def _entry(row: matrix.Row, done: set[str]) -> dict[str, object]:
    return {
        "posix": row.posix,
        "language": row.lang,
        "territory": row.terr,
        "codeset": row.codeset,
        "priority": row.prio,
        "generated": _key(row.posix) in done,
    }


def _count(rows: list[matrix.Row], prio: str) -> int:
    return sum(1 for row in rows if row.prio == prio)


def _key(name: str) -> str:
    """归一化 locale 名：源码目录、归档列表与矩阵三种写法可比。"""
    return name.lower().replace("-", "").replace(".", "")


def _have(root: str) -> set[str]:
    """已生成的 locale 名集合（目录与 locale-archive 两种落点）。"""
    base = Path(root) / LOCALE_DIR
    names: set[str] = set()
    if base.is_dir():
        names.update(_key(entry.name) for entry in base.iterdir())
    tool = shutil.which(LOCALEDEF)
    if root == ROOT and tool:
        out = _list_archive(tool)
        names.update(_key(line) for line in out.splitlines() if line.strip())
    return names


def _list_archive(tool: str) -> str:
    try:
        proc = subprocess.run([tool, "--list-archive"], capture_output=True,
                              text=True, check=False)
    except OSError:
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def _targets(args: argparse.Namespace, rows: list[matrix.Row]) -> list[matrix.Row]:
    if args.all:
        return rows
    known = {row.posix: row for row in rows}
    wanted = args.names or [row.posix for row in rows if row.prio == "P0"]
    unknown = [name for name in wanted if name not in known]
    if unknown:
        raise Fail(EXIT_POLICY, f"矩阵内没有: {' '.join(unknown)}")
    return [known[name] for name in wanted]


def _cmd(row: matrix.Row, root: str) -> list[str]:
    argv = [LOCALEDEF, "-i", row.source, "-f", row.codeset]
    if root != ROOT:
        argv.append(f"--prefix={root}")
    argv.append(row.posix)
    return argv


def _run(argv: list[str]) -> bool:
    tool = shutil.which(argv[0])
    if not tool:
        raise Fail(EXIT_ENV, "缺少 localedef")
    try:
        proc = subprocess.run([tool, *argv[1:]], check=False)
    except OSError as exc:
        print(f"{argv[0]}: {exc}", file=sys.stderr)
        return False
    return proc.returncode == 0


def _conf_bad(path: Path, rows: list[matrix.Row], need: bool) -> list[str]:
    if not path.is_file():
        if need:
            raise Fail(EXIT_ENV, f"没有 locale 配置: {path}")
        return []
    return matrix.conf_problems(matrix.read_conf(path), rows)
