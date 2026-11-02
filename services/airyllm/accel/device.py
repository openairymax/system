# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 加速设备节点的可用性判定
#
# 节点存在不等于可用：服务身份没有读写权限时，引擎会在初始化阶段失败
# 退出，而状态层已经报过一篇「探测到 GPU」。因此这里判的是当前进程能否
# 打开节点，并把依据一并带出，供降级日志逐条打印。
#
# 只判可读写，不判设备号与驱动版本：判据越接近「引擎能不能用这个节点」
# 越好，设备号说明不了这一点；驱动与权重的匹配问题留给引擎启动时自报。

"""Accelerator device node checks (K-22-03)."""

from __future__ import annotations

import os
from pathlib import Path

from . import fs

__all__ = ["DENIED", "MISSING", "OK", "renders", "usable"]

OK = "ok:"
MISSING = "missing:"
DENIED = "denied:"


def usable(base: Path, name: str) -> tuple[bool, str]:
    """判定设备节点当前身份可读写。

    Args:
        base: 运行根，通常是 ``/``。
        name: 相对运行根的设备路径，如 ``dev/nvidia0``。

    Returns:
        ``(可用, 证据)``；证据为 ``ok:``、``missing:`` 或 ``denied:``
        加上该路径。
    """
    path = base / name
    if not path.exists():
        return False, MISSING + name
    if not _granted(path):
        return False, DENIED + name
    return True, OK + name


def renders(base: Path) -> tuple[tuple[bool, str], ...]:
    """逐个判定 DRM 渲染节点的可用性。

    多卡机器上一张卡不可用不应否决整个后端，故由调用方取「任一」。

    Args:
        base: 运行根，通常是 ``/``。

    Returns:
        每张渲染节点的 ``(可用, 证据)``；没有渲染节点时返回一条指向
        ``dev/dri/renderD*`` 的缺失证据。
    """
    found = fs.names(base / "dev/dri", "renderD*")
    if not found:
        return ((False, MISSING + "dev/dri/renderD*"),)
    return tuple(usable(base, f"dev/dri/{name}") for name in found)


def _granted(path: Path) -> bool:
    return os.access(path, os.R_OK | os.W_OK, effective_ids=True)
