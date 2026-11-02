# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 本地版本身份采集
#
# 版本身份是三元组：os-release 的 VERSION_ID、airymaxos-release 的
# Release、运行内核。三者各取自唯一事实源，不从 /etc/system-release
# 或 PRETTY_NAME 等投影反解——投影的格式不是契约。

"""Local AirymaxOS identity probe (V-14-05).

设计依据：docs-linux/190-distribution/04-update-mechanism.md §2.2。
本模块只回答「本机是什么」，不取网络、不比较版本；采集失败时抛
:class:`Probe`，由命令层决定退出码与提示文案。

"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from .identity import Identity, parse_build, parse_version, version_id

__all__ = ["OS_RELEASE", "PACKAGE", "Probe", "local_identity", "os_version",
           "rpm_release"]

OS_RELEASE = Path("/usr/lib/os-release")

PACKAGE = "airymaxos-release"

QUERY = "%{RELEASE}"

TIMEOUT = 15


class Probe(RuntimeError):
    """本地身份不可采集。"""


def os_version(path: Path = OS_RELEASE) -> str:
    """读取 os-release 的 VERSION_ID。

    Args:
        path: os-release 路径，缺省取唯一事实源。

    Returns:
        形如 ``0.0.4`` 的版本号。

    Raises:
        Probe: 文件不可读或 VERSION_ID 缺失、为空、格式不符。
    """
    try:
        text = version_id(path)
        parse_version(text)
        return text
    except (OSError, ValueError) as exc:
        raise Probe(f"os-release: {exc}") from exc


def rpm_release(name: str = PACKAGE) -> int:
    """查询发行包的构建号。

    Args:
        name: 包名，缺省为 airymaxos-release。

    Returns:
        ``%{RELEASE}`` 的前导整数段。

    Raises:
        Probe: rpm 不可执行、包未安装或 Release 不含整数。
    """
    try:
        proc = subprocess.run(
            ["rpm", "-q", "--qf", QUERY, name],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Probe(f"rpm -q {name}: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"
        raise Probe(f"rpm -q {name}: {detail}")
    try:
        return parse_build(proc.stdout)
    except ValueError as exc:
        raise Probe(f"rpm -q {name}: {exc}") from exc


def local_identity(path: Path = OS_RELEASE) -> Identity:
    """采集本机三元组。

    Args:
        path: os-release 路径，缺省取唯一事实源。

    Returns:
        version、build 与 kernel 组成的身份。

    Raises:
        Probe: 任一分量不可采集；不返回部分结果。
    """
    return Identity(os_version(path), rpm_release(), platform.release())
