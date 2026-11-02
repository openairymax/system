# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS Vulkan 驱动清单（ICD）判定
#
# Vulkan 的可用性由加载器目录里的 JSON 决定，而目录存在、JSON 存在都不
# 代表驱动库能加载：发行版卸载驱动后常留下指向已删除文件的清单，加载器
# 会把实例创建失败报给引擎。这里按库路径核实一遍，核不实的清单记为
# stale 并跳过，免得整机被一份死清单拖在「有 Vulkan」的错误结论上。
#
# 只认绝对路径与带目录的相对路径。裸 soname（如 libGLX_nvidia.so.0）由
# 加载器按自己的搜索路径解析，静态无法判定，记为 unverified 但接受——
# 否则 NVIDIA 与 AMD 的官方清单会被一律判死，整机白白降级到 CPU。

"""Vulkan ICD manifest checks (K-22-06)."""

from __future__ import annotations

import json
from pathlib import Path

from . import fs

__all__ = ["DIRS", "EMPTY", "FOUND", "MISSING", "STALE", "UNVERIFIED",
           "first"]

DIRS = (
    "usr/share/vulkan/icd.d",
    "usr/local/share/vulkan/icd.d",
    "etc/vulkan/icd.d",
)

FOUND = "ok:"
MISSING = "missing:"
EMPTY = "empty:"
STALE = "stale:"
UNVERIFIED = "unverified:"


def first(base: Path) -> tuple[bool, str]:
    """给出第一个可用的 ICD 清单及其证据。

    Args:
        base: 运行根，通常是 ``/``。

    Returns:
        ``(可用, 证据)``；不可用时证据指向实际看到的第一个问题——缺失
        目录、空目录或核不实的清单。
    """
    evidence = MISSING + DIRS[0]
    for name in DIRS:
        ok, why = _scan(base, name)
        if ok:
            return True, why
        if not why.startswith(MISSING):
            evidence = why
    return False, evidence


def _scan(base: Path, name: str) -> tuple[bool, str]:
    directory = base / name
    if not directory.is_dir():
        return False, MISSING + name
    found = fs.names(directory, "*.json")
    if not found:
        return False, EMPTY + name
    evidence = STALE + f"{name}/{found[0]}"
    for item in found:
        ok, why = _library(base, f"{name}/{item}")
        if ok:
            return True, why
        evidence = why
    return False, evidence


def _library(base: Path, label: str) -> tuple[bool, str]:
    library = _path(base / label)
    if not library:
        return False, STALE + label
    if "$" in library or "/" not in library:
        return True, UNVERIFIED + label
    if not (base / library.lstrip("/")).exists():
        return False, STALE + label
    return True, FOUND + label


def _path(item: Path) -> str:
    """取出清单里的 ``ICD.library_path``；结构不符时为空串。"""
    try:
        payload = json.loads(item.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    icd = payload.get("ICD") if isinstance(payload, dict) else None
    library = icd.get("library_path") if isinstance(icd, dict) else None
    return library if isinstance(library, str) else ""
