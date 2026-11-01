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
钉住，供 ``airy-disk status --json`` 与安装器 ``%pre`` 共用。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

__all__ = [
    "DATA_MOUNT",
    "ESP_MOUNT",
    "SCHEMA_VERSION",
    "SYSTEM_MOUNTS",
    "Bus",
    "Disk",
    "FsKind",
    "Partition",
    "Role",
    "TableKind",
    "TopoKind",
    "Topology",
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
