# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 磁盘拓扑探测层
#
# 以 lsblk -J -b 为唯一观测源，把块设备树归一为 model 的只读快照。
# 归一用查表而非分支：上游 token 映射到枚举，未知值落 OTHER/UNKNOWN
# 而不报错，新型总线或文件系统出现时探测仍可用；新增识别只扩表，
# 不改判定路径（J-33）。

"""AirymaxOS disk topology probing (M-30-02).

设计依据：docs-linux/190-distribution/03-installer-design.md §3 与
0.0.4 方案 §6.3 M-30。``parse()`` 是纯函数，输入 lsblk 的 JSON 文本，
输出 :class:`Topology`；``probe()`` 只负责取回该文本。两者分离让
判定可离线复算，也让测试不依赖目标机。

字段单位与形态以 ``lsblk -b -J`` 为准：``size`` 为字节数，``ro`` 与
``rm`` 为布尔，``tran``/``pttype``/``fstype`` 在虚拟设备上可能为
null，swap 的挂载点是字面量 ``[SWAP]`` 而非路径。
"""

from __future__ import annotations

import json
import subprocess

from .model import (
    BUS_TOKENS,
    DATA_MOUNT,
    ESP_MOUNT,
    FS_TOKENS,
    SYSTEM_MOUNTS,
    TABLE_TOKENS,
    Bus,
    Disk,
    FsKind,
    Partition,
    Role,
    TableKind,
    TopoKind,
    Topology,
)

__all__ = ["COLUMNS", "TIMEOUT", "host", "parse", "probe"]

COLUMNS = (
    "NAME,SIZE,TYPE,PTTYPE,FSTYPE,MOUNTPOINT,LABEL,PARTUUID,"
    "RO,RM,MODEL,SERIAL,TRAN"
)

TIMEOUT = 30

TRUE = frozenset({"true", "1", "yes"})

MOUNT_ROLE = {ESP_MOUNT: Role.ESP, DATA_MOUNT: Role.DATA}

FS_ROLE = {FsKind.SWAP: Role.SWAP}


def parse(payload: str) -> Topology:
    """把 lsblk 的 JSON 文本归一为拓扑快照。"""
    data = json.loads(payload or "{}")
    nodes = data.get("blockdevices") or ()
    disks = tuple(_disk(node) for node in nodes if _kind_of(node) == "disk")
    return Topology(kind=_topo(disks), disks=disks)


def probe(timeout: int = TIMEOUT) -> Topology:
    """在本机执行 lsblk 并解析结果。"""
    argv = ["lsblk", "-J", "-b", "-o", COLUMNS]
    done = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=True
    )
    return parse(done.stdout)


def host(topo: Topology, mount: str) -> Disk | None:
    """返回承载某挂载点的磁盘，未挂载时为 None。

    安装介质里 "/" 由 overlay 提供，本就没有承载磁盘，此时返回 None
    是事实而非失败：调用方必须据此转入手工选盘，不能拿第一块盘顶替。
    """
    for disk in topo.disks:
        if any(part.mount == mount for part in disk.parts):
            return disk
    return None


def _text(node: dict, key: str) -> str:
    """取文本字段；null 与缺失都归一为空串。"""
    value = node.get(key)
    return "" if value is None else str(value).strip()


def _flag(node: dict, key: str) -> bool:
    """取布尔字段；JSON 布尔与 ``true`` 字面量都接受。"""
    value = node.get(key)
    return value is True or _text(node, key).lower() in TRUE


def _kind_of(node: dict) -> str:
    return _text(node, "type")


def _role(mount: str | None, fs: FsKind) -> Role:
    """挂载点优先于文件系统：数据盘可换文件系统，角色不变。"""
    if mount in MOUNT_ROLE:
        return MOUNT_ROLE[mount]
    if mount in SYSTEM_MOUNTS:
        return Role.SYSTEM
    return FS_ROLE.get(fs, Role.FOREIGN)


def _part(node: dict) -> Partition:
    fs = FS_TOKENS.get(_text(node, "fstype"), FsKind.OTHER)
    mount = _text(node, "mountpoint") or None
    if mount == "[SWAP]":
        mount = None
    return Partition(
        name=_text(node, "name"),
        size=int(node.get("size") or 0),
        fs=fs,
        mount=mount,
        label=_text(node, "label") or None,
        partuuid=_text(node, "partuuid") or None,
        role=_role(mount, fs),
    )


def _disk(node: dict) -> Disk:
    parts = tuple(
        _part(child)
        for child in node.get("children") or ()
        if _kind_of(child) == "part"
    )
    return Disk(
        name=_text(node, "name"),
        size=int(node.get("size") or 0),
        bus=BUS_TOKENS.get(_text(node, "tran"), Bus.OTHER),
        table=TABLE_TOKENS.get(_text(node, "pttype"), TableKind.UNKNOWN),
        ro=_flag(node, "ro"),
        removable=_flag(node, "rm"),
        model=_text(node, "model"),
        serial=_text(node, "serial"),
        parts=parts,
    )


def _foreign(disk: Disk) -> bool:
    """外来分区指有文件系统但不在本方布局中的分区。"""
    return any(
        part.role is Role.FOREIGN and part.fs != FsKind.NONE
        for part in disk.parts
    )


def _topo(disks: tuple[Disk, ...]) -> TopoKind:
    """外来分区一律判双系统：宁可少给动作，不可误清用户数据。"""
    if not disks:
        return TopoKind.EMPTY
    if any(_foreign(disk) for disk in disks):
        return TopoKind.DUAL_BOOT
    if len(disks) == 1:
        return TopoKind.SINGLE
    return TopoKind.MULTI
