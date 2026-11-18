# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 注入通道的现状与写入试探
#
# 两条既有通道：securityfs 的 policy 表与 procfs 的 agentrt 目录。前者
# 0600，写入侧接受 `rule <hook> <field>=<value> <allow|deny>`；后者仍是
# 0444 只读导出就绪标记。探针不把「可写」当作「已配置」：记录权限位、
# 发起一次写入、把内核返回的 errno 名称原样带回。
#
# 试探只做「打开并写入空串」。空写入在 policy 上是合法 no-op（返回 0），
# 一次成功即证明写 fops 在位；agentrt 侧预期 EACCES，失败本身也是证据。
# 不提交任何规则文本，取证因此不改变机器的策略状态。

"""AirymaxOS injection channels."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import files
from .paths import Paths

__all__ = ["Channel", "Outcome", "attempt", "inspect"]

POLICY = "policy"

AGENTRT = "agentrt"

WRITE_BIT = 0o200

MISSING = "missing"


@dataclass(frozen=True, slots=True)
class Channel:
    """一条注入通道的权限面与存在性。"""

    name: str
    path: str
    mode: str | None
    present: bool
    writable: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class Outcome:
    """一次写入试探的结果；error 为内核给出的 errno 名称。"""

    channel: str
    path: str
    error: str | None
    delivered: bool


def inspect(paths: Paths) -> tuple[Channel, ...]:
    """只读检查两条通道，不发起写入。"""
    return (
        _chan(POLICY, Path(paths.policy)),
        _chan(AGENTRT, Path(paths.agentrt)),
    )


def attempt(paths: Paths) -> tuple[Outcome, ...]:
    """向两条通道各发起一次空写入，成功与否都留证。"""
    return _push(POLICY, Path(paths.policy)), _push(
        AGENTRT, Path(paths.agentrt) / "present"
    )


def _chan(name: str, path: Path) -> Channel:
    mode = files.mode(path)
    if mode is None:
        return Channel(name, str(path), None, False, False, MISSING)
    writable = bool(int(mode, 8) & WRITE_BIT)
    return Channel(name, str(path), mode, True, writable, None)


def _push(name: str, path: Path) -> Outcome:
    error = files.write(path, "")
    return Outcome(name, str(path), error, error is None)