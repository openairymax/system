# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 数据分区健康判定
#
# 容量与只读标志只有内核知道，取自 statvfs，与 df 同源；拓扑只用来给
# 分区命名。两者分开的理由是 /data 未挂载时目录仍存在于根分区，
# statvfs 会照常返回根分区的余量，若只看容量就会把「没挂上」报成健康。
# 因此先认拓扑中的挂载事实，再用 ismount 复核，最后才读容量。
#
# 只读是扩容的前置：ext4 遇到 I/O 错误或 fsck 失败会 remount-ro，
# 此时数据仍在，但任何写入都会失败。判 read-only 而不是 low-space，
# 让运维先修文件系统，再去扩容。
#
# 判据④：每种不健康都给出可执行建议，取证命令带具体设备节点；建议文本
# 与判定 token 分离，门禁只读 token，不解析中文。

"""AirymaxOS /data health inspection (K-24).

设计依据：docs-linux/190-distribution/03-installer-design.md §3.6 与
0.0.4 方案 §6.3 K-24。:func:`inspect` 输入探测快照与本机 statvfs，
输出判定 token、容量事实与修复建议；不做任何写操作。

"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from os import ST_RDONLY, statvfs
from os.path import ismount

from .model import DATA_MOUNT, FsKind, Partition, Topology

__all__ = ["LIMIT", "Health", "State", "dump", "inspect", "remedy"]

LIMIT = 90

# 不可读时的取证命令：只做只读检查，不改动数据。
FS_CHECK = {
    FsKind.EXT4: "fsck.ext4 -f",
    FsKind.XFS: "xfs_repair -n",
    FsKind.BTRFS: "btrfs check",
}


@unique
class State(Enum):
    """健康判定；除 ``OK`` 外都带修复建议。"""

    OK = "ok"
    NO_DATA = "no-data"
    READ_ONLY = "read-only"
    LOW_SPACE = "low-space"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class Health:
    """一次健康判定；容量单位为字节，``used`` 为整数百分比。"""

    state: State
    part: str = ""
    total: int = 0
    free: int = 0
    used: int = 0
    advice: str = ""

    @property
    def ok(self) -> bool:
        return self.state is State.OK


def inspect(topo: Topology, path: str = DATA_MOUNT) -> Health:
    """判定 /data 的挂载、只读与容量状况。

    拓扑认为已挂载而内核未挂载时判 unavailable：装机记录过期或挂载点
    被卸载时，宁可报不可用，也不拿根分区的余量冒充数据分区。
    """
    part = _data(topo)
    if part is None:
        return Health(State.NO_DATA, advice=remedy(State.NO_DATA))
    if not ismount(path):
        return Health(State.UNAVAILABLE, part.name,
                      advice=remedy(State.UNAVAILABLE, part))
    try:
        vfs = statvfs(path)
    except OSError:
        return Health(State.UNAVAILABLE, part.name,
                      advice=remedy(State.UNAVAILABLE, part))
    total = vfs.f_blocks * vfs.f_frsize
    free = vfs.f_bavail * vfs.f_frsize
    if total <= 0:
        return Health(State.UNAVAILABLE, part.name,
                      advice=remedy(State.UNAVAILABLE, part))
    used = _pct(vfs.f_blocks - vfs.f_bfree, vfs.f_bavail)
    if vfs.f_flag & ST_RDONLY:
        return Health(State.READ_ONLY, part.name, total, free, used,
                      remedy(State.READ_ONLY))
    if used >= LIMIT:
        return Health(State.LOW_SPACE, part.name, total, free, used,
                      remedy(State.LOW_SPACE, part, used))
    return Health(State.OK, part.name, total, free, used)


def remedy(state: State, part: Partition | None = None,
           used: int = 0) -> str:
    """判定不健康的下一步；建议只描述动作，不改动任何东西。"""
    if state is State.NO_DATA:
        return (
            "未发现挂载在 /data 的数据分区：按 03-installer-design.md "
            "§3.6 的布局创建后，用 airy-disk status 复核"
        )
    if state is State.READ_ONLY:
        return (
            "数据分区以只读挂载：查看 dmesg 的文件系统错误，修复后执行 "
            "mount -o remount,rw /data"
        )
    if state is State.LOW_SPACE:
        return (
            f"使用率 {used}% 已达 {LIMIT}%：清理不可回收数据后，用 "
            "airy-disk grow --dry-run 复核扩容计划"
        )
    check = "fsck"
    if part is not None:
        check = FS_CHECK.get(part.fs, check)
    node = f"/dev/{part.name}" if part is not None else DATA_MOUNT
    return f"无法读取 {DATA_MOUNT}：先 {check} {node} 取证，再确认挂载"


def dump(found: Health) -> dict[str, object]:
    """序列化判定结果，供 ``airy-disk --json health`` 输出。"""
    return {
        "verdict": found.state.value,
        "part": found.part,
        "total": found.total,
        "free": found.free,
        "used": found.used,
        "advice": found.advice,
    }


def _data(topo: Topology) -> Partition | None:
    for disk in topo.disks:
        for part in disk.parts:
            if part.mount == DATA_MOUNT:
                return part
    return None


def _pct(used: int, avail: int) -> int:
    """已用百分比，口径同 df：分母是「已用 + 非特权可用」。"""
    if used <= 0:
        return 0
    return used * 100 // (used + avail)
