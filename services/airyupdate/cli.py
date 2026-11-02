# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 升级检查命令行入口
#
# check 取通道、比对三元组并原子写状态，退出码 0 最新 / 10 有新版 /
# 1 失败；status 只读状态与本地身份，退出码 0 有状态 / 1 无状态；
# notify 只读状态并在会话内提醒一次，永远返回 0。网络与状态属系统，
# 提醒属会话，故提醒单独成子命令，不为取件失败承担告警职责。

"""AirymaxOS update command line (V-14-07, V-14-08).

设计依据：docs-linux/190-distribution/04-update-mechanism.md §2.4、§2.5。
``check`` 无网络时判失败而非通过：把「没连上」记成「已是最新」是唯一
无法靠人工复核发现的假绿。``notify`` 不取网络、不解读失败原因，只因
状态里已有更新结论而提醒，故它与 ``apply`` 都不改 ``last_ok``。

"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from . import local, net, state
from .identity import Verdict, compare
from .model import EXIT_FAIL, EXIT_NEW, EXIT_OK, STATE_DIR, Identity

__all__ = ["main"]

STATE_HOME = Path(STATE_DIR)

SEND_TIMEOUT = 15

APP_NAME = "AirymaxOS"

ICON_NAME = "system-software-update"


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


def _key(found: state.Finding) -> str:
    return f"{found.version}/{found.build}"


def _send(found: state.Finding) -> bool:
    """发一次桌面通知；失败只告警，不阻断会话。"""
    title = f"{APP_NAME} {found.version} 可用"
    body = f"构建 {found.build}；跨版本升级请使用新的安装介质。"
    try:
        done = subprocess.run(
            ["notify-send", f"--app-name={APP_NAME}",
             f"--icon={ICON_NAME}", title, body],
            capture_output=True, text=True, timeout=SEND_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"airy-update: notify: {exc}", file=sys.stderr)
        return False
    if done.returncode != 0:
        reason = " ".join(done.stderr.split()) or f"exit {done.returncode}"
        print(f"airy-update: notify: {reason}", file=sys.stderr)
        return False
    return True


def _notify(args: argparse.Namespace) -> int:
    """有新版本且未提醒过则提醒一次；任何异常都不改变退出码。"""
    path = state.state_path(Path(args.state_dir))
    try:
        current = state.load(path)
    except state.Corrupt as exc:
        print(f"airy-update: state: {exc}", file=sys.stderr)
        return EXIT_OK
    found = current.last_ok
    if found is None:
        return EXIT_OK
    try:
        here = local.local_identity(Path(args.os_release))
    except local.Probe as exc:
        print(f"airy-update: {exc}", file=sys.stderr)
        return EXIT_OK
    remote = Identity(found.version, found.build, here.kernel)
    key = _key(found)
    if compare(here, remote) is not Verdict.NEWER or current.notified == key:
        return EXIT_OK
    if _send(found):
        _store(path, state.record_sent(current, key))
    return EXIT_OK


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
    notify = sub.add_parser("notify", help="有新版本时在会话内提醒一次")
    notify.set_defaults(func=_notify)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行并分派；返回值即进程退出码。"""
    args = _args(argv)
    return args.func(args)
