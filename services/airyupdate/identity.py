# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 版本身份解析与比较
#
# 版本号按三段数值比较，构建号按整数段比较，内核只作诊断。三个来源
# 各自唯一：version 取 os-release 的 VERSION_ID，build 取
# airymaxos-release 的 Release，kernel 取 uname -r；派生字段
# （PRETTY_NAME、/etc/system-release）不参与解析。

"""AirymaxOS identity parsing and comparison (V-14-02).

设计依据：docs-linux/190-distribution/04-update-mechanism.md §2.2。
同版本不同构建是 0.0.x 的真实形态，故 build 前进只判「同版本重建」，
不得表述为「有新版本」；版本号按数值而非字符串比较，前导零不影响
结论。内核变化不参与判定，只用于诊断与清单对照。

"""

from __future__ import annotations

import re
from enum import Enum, unique
from pathlib import Path

from .model import Identity

__all__ = [
    "Verdict",
    "compare",
    "parse_build",
    "parse_version",
    "version_id",
]

_VERSION = re.compile(r"^\d+\.\d+\.\d+$")

_BUILD = re.compile(r"^\s*(\d+)")

_QUOTES = "\"'"


@unique
class Verdict(Enum):
    """本地身份与通道身份的比照结论。"""

    SAME = "same"
    REBUILD = "rebuild"
    NEWER = "newer"
    OLDER = "older"


def parse_version(text: str) -> tuple[int, int, int]:
    """把三段版本号解析为可比数值。

    Args:
        text: 形如 ``0.0.4`` 的版本串，允许前导零。

    Returns:
        三段整数元组。

    Raises:
        ValueError: 段数不为三或含非数字字符。
    """
    value = text.strip()
    if not _VERSION.match(value):
        raise ValueError(f"not a three-segment version: {text!r}")
    major, minor, patch = value.split(".")
    return (int(major), int(minor), int(patch))


def parse_build(text: str) -> int:
    """取 Release 的整数段作为构建号。

    Args:
        text: ``rpm -q --qf '%{RELEASE}'`` 的输出。

    Returns:
        前导整数段的数值。

    Raises:
        ValueError: 不含整数段。
    """
    match = _BUILD.match(text)
    if match is None:
        raise ValueError(f"release has no integer segment: {text!r}")
    return int(match.group(1))


def version_id(path: Path) -> str:
    """读取 os-release 的 VERSION_ID。

    Args:
        path: os-release 文件路径。

    Returns:
        去引号后的版本串。

    Raises:
        ValueError: 文件缺少 VERSION_ID 或值为空。
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "VERSION_ID":
            text = value.strip().strip(_QUOTES)
            if text:
                return text
            break
    raise ValueError(f"{path}: no usable VERSION_ID")


def compare(local: Identity, remote: Identity) -> Verdict:
    """比较本地与通道的版本身份。

    Args:
        local: 本地身份三元组。
        remote: 通道清单给出的身份三元组。

    Returns:
        相同、同版本重建、通道更新或通道更旧。
    """
    here = parse_version(local.version)
    there = parse_version(remote.version)
    if there != here:
        return Verdict.NEWER if there > here else Verdict.OLDER
    if remote.build != local.build:
        return Verdict.REBUILD if remote.build > local.build else Verdict.OLDER
    return Verdict.SAME
