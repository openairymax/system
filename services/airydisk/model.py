# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 磁盘设备模型
#
# 探测层、操作层与磁盘服务共用的只读数据契约：磁盘、分区与拓扑的
# 类型声明，以及它们与 sysfs/lsblk 字段 token 的一一映射。设备只用
# 容量、总线、分区表、文件系统与挂载点描述，不含机型、品牌或盘符
# 分支，新增拓扑只加数据不改代码路径（J-33）。

"""AirymaxOS disk device model types (M-30-01).

设计依据：docs-linux/190-distribution/03-installer-design.md §3
（分区方案）与 0.0.4 方案 §6.3 M-30。序列化形态由 ``SCHEMA_VERSION``
钉住，供 ``airy-disk status --json`` 与安装器 ``%pre`` 共用：
:func:`dump` 与 :func:`load` 互为逆运算，token 映射表由本模块持有，
探测层只做字段读取。

"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum, unique

__all__ = [
    "BUS_TOKENS",
    "DATA_MOUNT",
    "ESP_MOUNT",
    "FS_TOKENS",
    "SCHEMA_VERSION",
    "SYSTEM_MOUNTS",
    "TABLE_TOKENS",
    "Bus",
    "Disk",
    "FsKind",
    "Partition",
    "Role",
    "TableKind",
    "TopoKind",
    "Topology",
    "dump",
    "load",
    "order",
]

SCHEMA_VERSION = 1

DATA_MOUNT = "/data"

ESP_MOUNT = "/boot/efi"

SYSTEM_MOUNTS = ("/", "/boot", ESP_MOUNT, "/usr", "/var")


@unique
class Bus(Enum):
    """块设备承载总线。"""

    NVME = "nvme"
    SATA = "sata"
    SCSI = "scsi"
    USB = "usb"
    MMC = "mmc"
    VIRTIO = "virtio"
    OTHER = "other"


@unique
class TableKind(Enum):
    """分区表类型，取值与 ``lsblk -J`` 的 ``pttype`` 一致。"""

    GPT = "gpt"
    MBR = "dos"
    NONE = ""
    UNKNOWN = "unknown"


@unique
class FsKind(Enum):
    """分区文件系统，取值与 ``lsblk -J`` 的 ``fstype`` 一致。"""

    EXT4 = "ext4"
    XFS = "xfs"
    BTRFS = "btrfs"
    VFAT = "vfat"
    NTFS = "ntfs"
    SWAP = "swap"
    NONE = ""
    OTHER = "other"


@unique
class Role(Enum):
    """分区在 AirymaxOS 布局中的角色，系统与数据分区独立挂载。"""

    SYSTEM = "system"
    DATA = "data"
    ESP = "esp"
    SWAP = "swap"
    FOREIGN = "foreign"


@unique
class TopoKind(Enum):
    """拓扑分类，裸盘、多盘与双系统共用一条代码路径。"""

    EMPTY = "empty"
    SINGLE = "single"
    MULTI = "multi"
    DUAL_BOOT = "dual-boot"


BUS_TOKENS = {bus.value: bus for bus in Bus}

FS_TOKENS = {fs.value: fs for fs in FsKind}

TABLE_TOKENS = {table.value: table for table in TableKind}

PART_NUM = re.compile(r"(\d+)$")


@dataclass(frozen=True, slots=True)
class Partition:
    """单个分区的观测结果；``size`` 单位为字节。"""

    name: str
    size: int
    fs: FsKind
    mount: str | None = None
    label: str | None = None
    partuuid: str | None = None
    role: Role = Role.FOREIGN


@dataclass(frozen=True, slots=True)
class Disk:
    """单块磁盘的观测结果；``parts`` 按分区号升序。"""

    name: str
    size: int
    bus: Bus
    table: TableKind
    ro: bool = False
    removable: bool = False
    model: str = ""
    serial: str = ""
    parts: tuple[Partition, ...] = ()


@dataclass(frozen=True, slots=True)
class Topology:
    """一次探测的完整结果，``kind`` 决定操作面允许的动作集。"""

    kind: TopoKind
    disks: tuple[Disk, ...] = ()
    schema: int = SCHEMA_VERSION


def order(parts: Iterable[Partition]) -> tuple[Partition, ...]:
    """按分区号升序排列：内核名末尾的数字是唯一序号来源。

    扩容只对盘上最后一号分区成立，判定因此依赖次序；次序在这里归一，
    探测层与快照反序列化共用同一实现，避免两处各排一次或各自漏排。
    """
    return tuple(sorted(parts, key=_num))


def _num(part: Partition) -> int:
    found = PART_NUM.search(part.name)
    return int(found.group(1)) if found else 0


def dump(topo: Topology) -> dict[str, object]:
    """把拓扑序列化为 JSON 可编码的字典。"""
    return {
        "schema": topo.schema,
        "kind": topo.kind.value,
        "disks": [_disk_obj(disk) for disk in topo.disks],
    }


def load(obj: Mapping[str, object]) -> Topology:
    """从 :func:`dump` 的产物重建拓扑；schema 不符即拒绝。"""
    schema = obj.get("schema")
    if schema != SCHEMA_VERSION:
        raise ValueError(f"schema {schema!r} != {SCHEMA_VERSION}")
    return Topology(
        kind=TopoKind(str(obj.get("kind"))),
        disks=tuple(_disk_from(item) for item in _items(obj.get("disks"))),
    )


def _items(value: object) -> tuple[Mapping[str, object], ...]:
    """取出子对象序列；缺失或非序列都当空，避免调用方逐个判空。"""
    return tuple(value) if isinstance(value, list) else ()


def _disk_obj(disk: Disk) -> dict[str, object]:
    return {
        "name": disk.name,
        "size": disk.size,
        "bus": disk.bus.value,
        "table": disk.table.value,
        "ro": disk.ro,
        "removable": disk.removable,
        "model": disk.model,
        "serial": disk.serial,
        "parts": [_part_obj(part) for part in disk.parts],
    }


def _part_obj(part: Partition) -> dict[str, object]:
    return {
        "name": part.name,
        "size": part.size,
        "fs": part.fs.value,
        "mount": part.mount,
        "label": part.label,
        "partuuid": part.partuuid,
        "role": part.role.value,
    }


def _disk_from(obj: Mapping[str, object]) -> Disk:
    return Disk(
        name=str(obj.get("name", "")),
        size=int(obj.get("size") or 0),
        bus=BUS_TOKENS.get(str(obj.get("bus")), Bus.OTHER),
        table=TABLE_TOKENS.get(str(obj.get("table")), TableKind.UNKNOWN),
        ro=bool(obj.get("ro")),
        removable=bool(obj.get("removable")),
        model=str(obj.get("model", "")),
        serial=str(obj.get("serial", "")),
        parts=order(_part_from(item) for item in _items(obj.get("parts"))),
    )


def _part_from(obj: Mapping[str, object]) -> Partition:
    return Partition(
        name=str(obj.get("name", "")),
        size=int(obj.get("size") or 0),
        fs=FS_TOKENS.get(str(obj.get("fs")), FsKind.OTHER),
        mount=_opt(obj.get("mount")),
        label=_opt(obj.get("label")),
        partuuid=_opt(obj.get("partuuid")),
        role=Role(str(obj.get("role", Role.FOREIGN.value))),
    )


def _opt(value: object) -> str | None:
    return None if value is None else str(value)

