# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.

"""airy-backup 命令行：创建、核验、恢复、清理与统计。

退出码与异常层级一致：2 输入不成立，3 仓库/台账不可用，4 完整性失败。
恢复在任何完整性检查失败时都以 4 退出，绝不「先解包再说」。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model import BadBackup, BadInput, Kind, NS, iso, parse_iso
from .store import DEFAULT_ROOT, Store


def _stamp(text: str | None) -> int | None:
    if text is None or not str(text).strip():
        return None
    raw = str(text).strip()
    if raw.isdigit():
        return int(raw) * NS if len(raw) <= 12 else int(raw)
    return parse_iso(raw)


def _pair(item: str) -> tuple[str, Path]:
    name, sep, path = item.partition("=")
    if not sep or not name or not path:
        raise BadInput(f"--source 需要 NAME=PATH 形式: {item!r}")
    return name, Path(path)


def _sources(values: list[str] | None) -> list[tuple[str, Path]] | None:
    if not values:
        return None
    pairs: list[tuple[str, Path]] = []
    for item in values:
        pairs.append(_pair(item))
    return pairs


def _dump(value: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _cmd_create(args: argparse.Namespace, store: Store) -> int:
    stamp = _stamp(args.at)
    rec = store.create(
        args.kind,
        sources=_sources(args.source),
        stamp=stamp,
        actor=args.actor,
        sign_key=args.sign_key,
        codec=args.codec,
    )
    if args.json:
        print(json.dumps(rec.to_dict(), ensure_ascii=False, sort_keys=True))
    else:
        print(f"已创建 {rec.backup_id} ({rec.kind.value})")
        print(f"  文件: {rec.file} ({rec.size_bytes} 字节, {rec.compression})")
        print(f"  sha256: {rec.sha256}")
    return 0


def _auto_kind(store: Store, moment: datetime, stamp: int,
               full_hour: int) -> Kind:
    if moment.hour != full_hour:
        return Kind.INCREMENTAL
    today = iso(stamp)[:10]
    for rec in store.records():
        if rec.kind is Kind.FULL and iso(rec.created_at)[:10] == today:
            return Kind.INCREMENTAL
    return Kind.FULL


def _auto_extra(store: Store, moment: datetime, stamp: int,
                args: argparse.Namespace) -> dict[str, Any]:
    steps: dict[str, Any] = {}
    if moment.hour == args.prune_hour:
        steps["prune"] = store.prune(stamp=stamp, apply=True)
    if moment.weekday() == 6 and moment.hour == args.verify_hour:
        steps["verify"] = store.verify_all(deep=False)
    return steps


def _auto_show(steps: dict[str, Any], rec: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(steps, ensure_ascii=False, sort_keys=True))
        return
    print(f"已创建 {rec.backup_id} ({rec.kind.value})")
    for name in ("prune", "verify"):
        if name in steps:
            print(f"{name}: {json.dumps(steps[name], ensure_ascii=False)}")


def _cmd_auto(args: argparse.Namespace, store: Store) -> int:
    stamp = int(_stamp(args.at) or time.time_ns())
    moment = datetime.fromtimestamp(stamp // NS, timezone.utc)
    kind = _auto_kind(store, moment, stamp, args.full_hour)
    rec = store.create(kind, sources=_sources(args.source), stamp=stamp,
                       actor=args.actor, sign_key=args.sign_key,
                       codec=args.codec)
    steps: dict[str, Any] = {"backup": rec.to_dict()}
    steps.update(_auto_extra(store, moment, stamp, args))
    _auto_show(steps, rec, args.json)
    return 0


def _pick(recs: list[Any], kind: str | None,
          limit: int | None) -> list[Any]:
    if kind:
        want = Kind.parse(kind)
        recs = [rec for rec in recs if rec.kind is want]
    if limit:
        recs = recs[-limit:]
    return recs


def _cmd_list(args: argparse.Namespace, store: Store) -> int:
    recs = _pick(store.records(), args.kind, args.limit)
    if args.json:
        print(json.dumps([rec.to_dict() for rec in recs],
                         ensure_ascii=False, sort_keys=True))
        return 0
    for rec in recs:
        print(f"{rec.backup_id}\t{rec.kind.value}\t{iso(rec.created_at)}\t"
              f"{rec.size_bytes}\t{rec.file}")
    return 0


def _cmd_show(args: argparse.Namespace, store: Store) -> int:
    line = json.dumps(store.load(args.backup_id).to_dict(),
                      ensure_ascii=False, indent=2, sort_keys=True)
    print(line)
    return 0


def _show_checks(checks: list[dict[str, Any]], indent: str = "") -> None:
    for check in checks:
        flag = "ok" if check["ok"] else "FAIL"
        print(f"{indent}[{flag}] {check['check']} {check['detail']}")


def _show_all(results: dict[str, Any]) -> None:
    for ident, result in results.items():
        mark = "ok" if result["ok"] else "FAIL"
        print(f"[{mark}] {ident}")
        _show_checks(result["checks"], "  ")


def _verify_all(args: argparse.Namespace, store: Store) -> int:
    results = store.verify_all(pub_key=args.pub_key, deep=args.deep)
    ok = all(item["ok"] for item in results.values())
    if args.json:
        print(json.dumps(results, ensure_ascii=False, sort_keys=True))
    else:
        _show_all(results)
    return 0 if ok else 4


def _cmd_verify(args: argparse.Namespace, store: Store) -> int:
    if args.all:
        return _verify_all(args, store)
    if not args.backup_id:
        raise BadInput("verify 需要备份编号，或用 --all")
    result = store.verify(args.backup_id, pub_key=args.pub_key, deep=args.deep)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    else:
        _show_checks(result.checks)
    return 0 if result.ok else 4


def _cmd_restore(args: argparse.Namespace, store: Store) -> int:
    if not args.target:
        raise BadInput("restore 需要 --target")
    record = store.restore(
        args.backup_id,
        target=args.target,
        stamp=_stamp(args.at),
        actor=args.actor,
        agent_id=args.agent_id,
        confirm=args.confirm,
        pub_key=args.pub_key,
        log_path=args.log,
    )
    _dump(record, args.json)
    return 0


def _cmd_prune(args: argparse.Namespace, store: Store) -> int:
    result = store.prune(stamp=_stamp(args.at), apply=args.apply)
    _dump(result, args.json)
    return 0


def _cmd_stats(args: argparse.Namespace, store: Store) -> int:
    _dump(store.stats(), args.json)
    return 0


def _tail_options() -> argparse.ArgumentParser:
    """子命令之后的同名全局选项。

    运维习惯把 ``--root`` / ``--actor`` 写在动词后面，argparse 默认只认
    动词之前的全局选项。这里用 SUPPRESS 默认值再声明一次：写在后面时
    覆盖，不写时保留顶层默认值，两种位置都能用。
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--root", default=argparse.SUPPRESS,
                        help="备份仓库根目录")
    parser.add_argument("--actor", default=argparse.SUPPRESS,
                        help="操作者（记入清单与恢复日志）")
    parser.add_argument("--at", default=argparse.SUPPRESS,
                        help="以该时刻代替当前时间（ISO 或秒）")
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airy-backup",
        description="AirymaxOS 备份与恢复（P-15）",
    )
    parser.add_argument("--root", default=str(DEFAULT_ROOT),
                        help="备份仓库根目录")
    parser.add_argument("--actor", default="", help="操作者（记入清单与恢复日志）")
    parser.add_argument("--at", default="", help="以该时刻代替当前时间（ISO 或秒）")
    tail = _tail_options()
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", parents=[tail], help="创建一次备份")
    create.add_argument("--kind", required=True,
                        choices=[kind.value for kind in Kind])
    create.add_argument("--source", action="append", default=None,
                        metavar="NAME=PATH", help="备份来源，可重复；缺省用标准对象表")
    create.add_argument("--codec", default="zstd", choices=("zstd", "gzip"))
    create.add_argument("--sign-key", default=None, help="openssl 私钥，用于生成 .sig")
    create.add_argument("--json", action="store_true")
    create.set_defaults(func=_cmd_create)

    auto = sub.add_parser("auto", parents=[tail],
                          help="按小时表决定全量/增量，并顺带清理与周检")
    auto.add_argument("--source", action="append", default=None,
                      metavar="NAME=PATH")
    auto.add_argument("--codec", default="zstd", choices=("zstd", "gzip"))
    auto.add_argument("--sign-key", default=None)
    auto.add_argument("--full-hour", type=int, default=1)
    auto.add_argument("--prune-hour", type=int, default=4)
    auto.add_argument("--verify-hour", type=int, default=5)
    auto.add_argument("--json", action="store_true")
    auto.set_defaults(func=_cmd_auto)

    listing = sub.add_parser("list", parents=[tail], help="列出备份")
    listing.add_argument("--kind", choices=[kind.value for kind in Kind])
    listing.add_argument("--limit", type=int, default=0)
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=_cmd_list)

    show = sub.add_parser("show", parents=[tail], help="查看单件清单")
    show.add_argument("backup_id")
    show.set_defaults(func=_cmd_show)

    verify = sub.add_parser("verify", parents=[tail],
                            help="核验摘要、体积、签名与结构")
    verify.add_argument("backup_id", nargs="?")
    verify.add_argument("--all", action="store_true")
    verify.add_argument("--deep", action="store_true", help="额外遍历归档结构")
    verify.add_argument("--pub-key", default=None)
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=_cmd_verify)

    restore = sub.add_parser("restore", parents=[tail],
                             help="核验通过后恢复")
    restore.add_argument("backup_id")
    restore.add_argument("--target", required=True)
    restore.add_argument("--agent-id", default=None)
    restore.add_argument("--confirm", action="store_true",
                         help="全量恢复必须显式确认")
    restore.add_argument("--pub-key", default=None)
    restore.add_argument("--log", default=None)
    restore.add_argument("--json", action="store_true")
    restore.set_defaults(func=_cmd_restore)

    prune = sub.add_parser("prune", parents=[tail], help="按保留期清理")
    prune.add_argument("--apply", action="store_true",
                       help="真正删除；不带时只列候选")
    prune.add_argument("--json", action="store_true")
    prune.set_defaults(func=_cmd_prune)

    stats = sub.add_parser("stats", parents=[tail], help="仓库统计")
    stats.add_argument("--json", action="store_true")
    stats.set_defaults(func=_cmd_stats)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = Store(Path(args.root))
    try:
        return int(args.func(args, store))
    except BadBackup as exc:
        print(f"airy-backup: {exc}", file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(f"airy-backup: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
