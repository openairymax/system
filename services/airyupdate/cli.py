# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 升级检查命令行入口
#
# check 取通道、比对三元组并原子写状态，退出码 0 最新 / 10 有新版 /
# 1 失败；status 只读状态与本地身份，退出码 0 有状态 / 1 无状态。
# 网络与状态属系统，提醒属会话，故本入口不弹通知，只给出可判读的结论。

"""AirymaxOS update command line (V-14-07).

设计依据：docs-linux/190-distribution/04-update-mechanism.md §2.4。
``check`` 无网络时判失败而非通过：把「没连上」记成「已是最新」是唯一
无法靠人工复核发现的假绿。

"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from . import local, net, state
from .identity import Verdict, compare
from .model import EXIT_FAIL, EXIT_NEW, EXIT_OK, STATE_DIR, Identity

__all__ = ["main"]

STATE_HOME = Path(STATE_DIR)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _prior(path: Path) -> state.State:
    """读取旧状态；损坏时告警并以空状态继续，成功检查会重写成合法文件。"""
    try:
        return state.load(path)
    except state.Corrupt as exc:
        print(f"airy-update: state: {exc}", file=sys.stderr)
        return state.State()


def _store(path: Path, value: state.State) -> None:
    """写状态；无权限不掩盖检查结论，只降级为告警。"""
    try:
        state.save(path, value)
    except OSError as exc:
        print(f"airy-update: state: {exc}", file=sys.stderr)


def _report(here: Identity, remote: Identity, verdict: Verdict) -> None:
    if verdict is Verdict.NEWER:
        print(f"new release: {remote.version} build {remote.build} "
              f"(running {here.version} build {here.build})")
    elif verdict is Verdict.REBUILD:
        print(f"same version, different build: {remote.version} "
              f"build {remote.build} (running build {here.build})")
    elif verdict is Verdict.OLDER:
        print(f"local build is ahead of the channel: {here.version} "
              f"build {here.build}")
    else:
        print(f"up to date: {here.version} build {here.build}")


def _check(args: argparse.Namespace) -> int:
    path = state.state_path(Path(args.state_dir))
    prior = _prior(path)
    try:
        here = local.local_identity(Path(args.os_release))
        remote = net.fetch_manifest()
    except (local.Probe, net.Fetch) as exc:
        reason = " ".join(str(exc).split())
        _store(path, state.record_error(prior, state.Failure(reason, _now())))
        print(f"airy-update: {reason}", file=sys.stderr)
        return EXIT_FAIL
    _store(path, state.record_ok(
        prior, state.Finding(remote.version, remote.build, _now())))
    verdict = compare(here, remote)
    _report(here, remote, verdict)
    return EXIT_NEW if verdict is Verdict.NEWER else EXIT_OK


def _status(args: argparse.Namespace) -> int:
    path = state.state_path(Path(args.state_dir))
    try:
        current = state.load(path)
    except state.Corrupt as exc:
        print(f"airy-update: state: {exc}", file=sys.stderr)
        return EXIT_FAIL
    try:
        here = local.local_identity(Path(args.os_release))
    except local.Probe as exc:
        print(f"airy-update: {exc}", file=sys.stderr)
        return EXIT_FAIL
    print(f"version: {here.version}")
    print(f"build: {here.build}")
    print(f"kernel: {here.kernel}")
    _dump(current)
    empty = current.last_ok is None and current.last_error is None
    return EXIT_FAIL if empty else EXIT_OK


def _dump(current: state.State) -> None:
    if current.last_ok is None:
        print("last_ok: none")
    else:
        item = current.last_ok
        print(f"last_ok: {item.version} build {item.build} at {item.checked_at}")
    if current.last_error is None:
        print("last_error: none")
    else:
        item = current.last_error
        print(f"last_error: {item.reason} at {item.failed_at}")
    print(f"notified: {current.notified or 'none'}")


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="airy-update",
        description="AirymaxOS 升级检查",
    )
    parser.set_defaults(state_dir=STATE_HOME, os_release=local.OS_RELEASE)
    sub = parser.add_subparsers(dest="cmd", required=True)
    check = sub.add_parser("check", help="取发行通道并比对当前版本")
    check.set_defaults(func=_check)
    status = sub.add_parser("status", help="显示本地身份与最近一次检查结论")
    status.set_defaults(func=_status)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行并分派；返回值即进程退出码。"""
    args = _args(argv)
    return args.func(args)
