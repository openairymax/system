# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# airy-pkg 命令行
#
# 桌面双击进来的文件按后缀只有两种：.deb 与 .rpm。前者没有 dpkg 可用，
# 交给 airy-deb 现场转成 RPM 再装；后者直接交给 dnf。分派放在这里，是因为
# 桌面的 MIME 注册只能指向一个程序，若让 .deb 与 .rpm 各指一个入口，
# 两边的回显、确认与提权迟早出现两套行为。
#
# 确认与提权都走 airydeb.interact：同一个问句、同一份答案判定、同一条
# pkexec 重入路径。非 root 且要求确认时先提权再读包，未提权成功前不碰
# 文件；.deb 分支提权后 euid 已是 0，airy-deb 不会二次提权。
#
# 退出码与 airy-deb 一致：0 成功；2 用法错误（后缀不认识、--ignore-scripts
# 用在 .rpm 上）；4 环境不具备（文件不可读、缺 airy-deb/rpm/dnf）；5 策略
# 拒绝（用户在确认处取消、无终端无法确认）；6 安装失败。

"""AirymaxOS local package install entry point."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from airydeb.err import (
    EXIT_BUILD,
    EXIT_ENV,
    EXIT_OK,
    EXIT_POLICY,
    EXIT_USAGE,
    Fault,
)
from airydeb.interact import ask, elevate

__all__ = [
    "EXIT_BUILD",
    "EXIT_ENV",
    "EXIT_OK",
    "EXIT_POLICY",
    "EXIT_USAGE",
    "main",
]

# rpm 的查询格式：名字、版本-发行、架构三段，空格分隔。安装前回显与
# 判负都读这一行，不再用 rpm -qpi 的多行输出做二次解析。
QF = "%{NAME} %{VERSION}-%{RELEASE} %{ARCH}"


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行并分派；返回值即进程退出码。"""
    args = _args(argv)
    try:
        return _run(args)
    except Fault as exc:
        print(f"airy-pkg: {exc}", file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(f"airy-pkg: {exc}", file=sys.stderr)
        return EXIT_ENV


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="airy-pkg",
        description="AirymaxOS 本地软件包安装（.deb 与 .rpm）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    put = sub.add_parser("install", help="安装本地的 .deb 或 .rpm")
    put.add_argument("file")
    put.add_argument("--ask", action="store_true",
                     help="回显并等待终端确认后才安装（桌面双击入口用）")
    put.add_argument("--ignore-scripts", action="store_true",
                     help="允许跳过 .deb 的 Debian 维护者脚本")
    return parser.parse_args(argv)


def _run(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.is_file():
        raise Fault(f"不是可读文件: {path}")
    kind = path.suffix.lower()
    if kind not in (".deb", ".rpm"):
        raise Fault(f"不认识的后缀 {kind or '(无)'}：只接受 .deb 与 .rpm",
                    EXIT_USAGE)
    if kind == ".rpm" and args.ignore_scripts:
        raise Fault("--ignore-scripts 只对 .deb 有意义", EXIT_USAGE)
    if args.ask and os.geteuid() != 0:
        return elevate("airy-pkg", _reenter(args))
    if kind == ".deb":
        return _deb(path, args)
    return _rpm(path, args)


def _reenter(args: argparse.Namespace) -> list[str]:
    """提权重入时的参数：文件取绝对路径，工作目录与相对路径无关。"""
    argv = ["install", str(Path(args.file).resolve())]
    if args.ask:
        argv.append("--ask")
    if args.ignore_scripts:
        argv.append("--ignore-scripts")
    return argv


def _deb(path: Path, args: argparse.Namespace) -> int:
    """转发给 airy-deb：转换、脚本取舍与回显都在那边，只有一份实现。"""
    tool = shutil.which("airy-deb")
    if tool is None:
        raise Fault("缺少 airy-deb，请安装 airymaxos-system 包")
    argv = [tool, "install", str(path)]
    if args.ignore_scripts:
        argv.append("--ignore-scripts")
    if args.ask:
        argv.append("--ask")
    return subprocess.run(argv, check=False).returncode


def _rpm(path: Path, args: argparse.Namespace) -> int:
    _need("rpm", "rpm")
    _need("dnf", "dnf")
    name, version, arch = _meta(path)
    if args.ask:
        _show(path, name, version, arch)
        if not ask("airy-pkg: 继续安装？[y/N] "):
            print("airy-pkg: 已取消，未做任何修改", file=sys.stderr)
            return EXIT_POLICY
    code = subprocess.run(["dnf", "install", "-y", str(path)],
                          check=False).returncode
    if code != 0:
        raise Fault(f"dnf 安装失败（退出 {code}）", EXIT_BUILD)
    return EXIT_OK


def _meta(path: Path) -> tuple[str, str, str]:
    done = subprocess.run(["rpm", "-qp", "--qf", QF + "\n", str(path)],
                          check=False, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True)
    parts = done.stdout.split()
    if done.returncode != 0 or len(parts) != 3:
        raise Fault(f"不是可读的 RPM: {path}")
    return parts[0], parts[1], parts[2]


def _show(path: Path, name: str, version: str, arch: str) -> None:
    print(f"package: {name} {version} ({arch})")
    print(f"rpm:     {path}")
    print("root:    /")
    print(f"+ dnf install -y {path}")


def _need(tool: str, package: str) -> None:
    if shutil.which(tool) is None:
        raise Fault(f"缺少 {tool}，请安装 {package} 包")
