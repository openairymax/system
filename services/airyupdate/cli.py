# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 升级检查命令行入口
#
# check 取通道、比对三元组并原子写状态，退出码 0 最新 / 10 有新版 /
# 2 未判定（没连上）/ 1 失败；远端尚未发布清单（HTTP 404）同样退出 0，
# 因为它等同于「暂无可装更新」而非故障。status 只读状态与本地身份，退出
# 码 0 有状态 / 1 无状态；notify 只读状态并在会话内提醒一次，永远返回 0；
# apply 只承担基底勘误，退出码 0 成功 / 1 失败 / 20 需重装介质。网络与
# 状态属系统，提醒属会话，故提醒单独成子命令，不为取件失败承担告警职责。

"""AirymaxOS update command line (V-14-07, V-14-08, V-14-09).

设计依据：docs-linux/190-distribution/04-update-mechanism.md §2.4、§2.5。
``check`` 无网络时判 2（未判定）而非通过，也不判 1：记成「已是最新」是
唯一无法靠人工复核发现的假绿，而记成失败会把「这台机器此刻没联网」写成
产品故障——定时单元在离线机器上每次开机都会执行，一个恒红的单元会盖住
真正的故障。反之，远端明确回 404（尚未发布清单）是连上了却没有可比对象，
属于正常起点，判通过。``notify`` 不取网络、不解读失败原因，
只因状态里已有更新结论而提醒，故它与 ``apply`` 都不改 ``last_ok``。
``apply`` 以 ``--assumeno`` 求解事务，再以 ``--cacheonly`` 对同一份元数据
执行：重新取元数据会让「看过的那份计划」和「执行的那份计划」不是一回事。

"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from . import local, net, state
from .identity import Verdict, compare
from .model import (EXIT_FAIL, EXIT_NEW, EXIT_OK, EXIT_REINSTALL,
                    EXIT_UNKNOWN, STATE_DIR, Identity)

__all__ = ["main"]

STATE_HOME = Path(STATE_DIR)

SEND_TIMEOUT = 15

APP_NAME = "AirymaxOS"

ICON_NAME = "system-software-update"

DNF_TIMEOUT = 3600

SOLVE = ["dnf", "--refresh", "--assumeno", "upgrade"]

RUN = ["dnf", "--cacheonly", "-y", "upgrade"]

ARCHS = frozenset({
    "aarch64", "i686", "loongarch64", "noarch", "ppc64le", "riscv64",
    "s390x", "x86_64",
})

SPAN = re.compile(r"sp(\d+)", re.IGNORECASE)


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


def _flat(exc: BaseException) -> str:
    """把异常压成单行：状态文件与日志都不接受换行。"""
    return " ".join(str(exc).split())


def _check(args: argparse.Namespace) -> int:
    path = state.state_path(Path(args.state_dir))
    prior = _prior(path)
    try:
        here = local.local_identity(Path(args.os_release))
        remote = net.fetch_manifest()
    except net.NoFeed as exc:
        # 渠道还没有清单 = 还没有可安装的更新。状态只记录真实结论：一次
        # 「上游尚未发版」不写 last_error（系统没有病），也不写 last_ok
        # （没有比对过任何版本）。
        print(f"airy-update: {_flat(exc)}")
        return EXIT_OK
    except net.Unreachable as exc:
        # 没连上：既不能报「已是最新」（假绿），也不该算产品故障。原因照
        # 样落 last_error，status 与日志仍能说明这次检查为何没结论。
        reason = _flat(exc)
        _store(path, state.record_error(prior, state.Failure(reason, _now())))
        print(f"airy-update: {reason}", file=sys.stderr)
        return EXIT_UNKNOWN
    except (local.Probe, net.Fetch) as exc:
        reason = _flat(exc)
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


class Dnf(RuntimeError):
    """dnf 不可执行（缺二进制、越权或超时）。"""


def _run(argv: list[str], capture: bool) -> tuple[int, str]:
    """执行 dnf；不可执行时抛 :class:`Dnf`，不把异常混进退出码。"""
    try:
        done = subprocess.run(argv, capture_output=capture, text=True,
                              timeout=DNF_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Dnf(str(exc)) from exc
    if not capture:
        return done.returncode, ""
    return done.returncode, f"{done.stdout}{done.stderr}"


def _plan(text: str) -> list[tuple[str, str]]:
    """从事务表里取出 (包名, EVR) 列表。

    dnf 的表格每行形如「名称 架构 EVR 仓库 大小」，架构列是唯一稳定的
    判别列：表头与汇总行都没有它。不解析列宽，避免版本号变长后错位。
    """
    rows = []
    for line in text.splitlines():
        cols = line.split()
        if len(cols) >= 4 and cols[1] in ARCHS and cols[2][:1].isdigit():
            rows.append((cols[0], cols[2]))
    return rows


def _sp(text: str) -> int | None:
    match = SPAN.search(text)
    return int(match.group(1)) if match else None


def _cross(rows: list[tuple[str, str]], here: int | None) -> bool:
    """事务是否跨服务包段迁移。

    基底发行包自身被升级即视为基底版本跃迁：段内勘误不会更换发行包。
    再以 EVR 里的 ``spN`` 段号兜底，本机段号不可得时不作此判定。
    """
    for name, evr in rows:
        if name == local.BASE:
            return True
        span = _sp(evr)
        if span is not None and here is not None and span != here:
            return True
    return False


def _reason(text: str, code: int) -> str:
    for line in text.splitlines():
        if "Error:" in line or "Failed" in line:
            return " ".join(line.split())
    lines = [line for line in text.splitlines() if line.strip()]
    return " ".join(lines[-1].split()) if lines else f"dnf exit {code}"


def _medium() -> None:
    """打印清单中的介质名与摘要；取不到清单不改变退出码。"""
    try:
        found = net.fetch_manifest()
    except net.Fetch as exc:
        print(f"airy-update: apply: {exc}", file=sys.stderr)
        return
    print(f"reinstall medium: {found.iso.name}")
    print(f"sha256: {found.iso.sha256}")
    print(f"release notes: {found.notes_url}")


def _apply(_args: argparse.Namespace) -> int:
    """求解事务计划并按同一计划执行；跨段迁移交回安装介质。"""
    if os.geteuid() != 0:
        print("airy-update: apply: root required", file=sys.stderr)
        return EXIT_FAIL
    try:
        code, text = _run(SOLVE, capture=True)
    except Dnf as exc:
        print(f"airy-update: apply: {exc}", file=sys.stderr)
        return EXIT_FAIL
    rows = _plan(text)
    if not rows:
        if "Nothing to do" in text:
            print("up to date: nothing to apply")
            return EXIT_OK
        print(f"airy-update: apply: {_reason(text, code)}", file=sys.stderr)
        return EXIT_FAIL
    if _cross(rows, local.base_span()):
        print("base moves across a service pack; dnf cannot carry it")
        _medium()
        return EXIT_REINSTALL
    print(f"applying {len(rows)} package transaction")
    try:
        code, _ = _run(RUN, capture=False)
    except Dnf as exc:
        print(f"airy-update: apply: {exc}", file=sys.stderr)
        return EXIT_FAIL
    if code != 0:
        print(f"airy-update: apply: dnf exit {code}", file=sys.stderr)
        return EXIT_FAIL
    print("transaction complete; reboot if the kernel was replaced")
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
    worker = sub.add_parser("apply", help="执行基底勘误升级（需 root）")
    worker.set_defaults(func=_apply)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行并分派；返回值即进程退出码。"""
    args = _args(argv)
    return args.func(args)
