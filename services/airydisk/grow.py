# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 数据分区扩容
#
# 扩容只改一件事：把 /data 所在分区放大到盘尾，再让文件系统跟上。分区
# 之后还有别的分区时，放大会搬动它们，系统分区的起止扇区、UUID 与 fsck
# 结果随之改变——这正是 DISK-01 判据②要挡住的，因此这里直接判 not-last
# 并给出现场修正路径，而不是「尽量补一下」。
#
# 计划复用 ops.Plan，于是 --dry-run、二次确认与失败回滚只有一份实现。
# 步骤的 target 一律是承载改动的设备节点：扩分区是整盘，扩文件系统是该
# 分区；xfs 与 btrfs 的命令行收挂载点，argv 如实反映，target 仍指向设备。
#
# 判据④：判定不通过时给出可执行修复建议（advice），执行失败时按缺失的
# 工具或退出码给出下一步（repair），不把错误码原样甩给用户。

"""AirymaxOS /data growth planning (K-24).

设计依据：docs-linux/190-distribution/03-installer-design.md §3.6 与
0.0.4 方案 §6.3 K-24。:func:`plan` 是纯函数，输入探测快照输出计划与
判定 token；执行仍由 :mod:`airydisk.runner` 负责。

"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

from .model import DATA_MOUNT, Disk, FsKind, Partition, Topology, num
from .ops import Action, Plan, Step

__all__ = ["GROWPART", "Grow", "State", "plan", "repair", "remedy"]

GROWPART = "/usr/bin/growpart"

RESIZE2FS = "/usr/sbin/resize2fs"

XFS_GROWFS = "/usr/sbin/xfs_growfs"

BTRFS = "/usr/bin/btrfs"

# 缺失工具时的安装包：扩容失败最常见的原因是精简镜像没有装它们，
# 给出包名比给出退出码 127 更能让运维一步修好。
PACKAGE = {
    GROWPART: "cloud-utils-growpart",
    RESIZE2FS: "e2fsprogs",
    XFS_GROWFS: "xfsprogs",
    BTRFS: "btrfs-progs",
}

REMOTE = "无法刷新分区表时查看 dmesg 与 partprobe，确认内核已识别新尺寸后重试"


@unique
class State(Enum):
    """扩容判定；除 ``OK`` 外都不生成计划。"""

    OK = "ok"
    NO_DATA = "no-data"
    NOT_LAST = "not-last"
    NO_FS = "no-fs"


@dataclass(frozen=True, slots=True)
class Grow:
    """一次扩容判定；``plan`` 为空表示不值得或不能执行。"""

    state: State
    disk: str = ""
    part: str = ""
    fs: FsKind = FsKind.NONE
    plan: Plan | None = None
    advice: str = ""

    @property
    def ok(self) -> bool:
        return self.state is State.OK


def plan(topo: Topology) -> Grow:
    """判定 /data 能否原地扩容，并给出完整计划。"""
    found = _data(topo)
    if found is None:
        return Grow(State.NO_DATA, advice=remedy(State.NO_DATA))
    disk, part = found
    if disk.parts[-1].name != part.name:
        return Grow(State.NOT_LAST, disk.name, part.name, part.fs,
                    advice=remedy(State.NOT_LAST))
    steps = _steps(disk.name, part)
    if not steps:
        return Grow(State.NO_FS, disk.name, part.name, part.fs,
                    advice=remedy(State.NO_FS, part.fs))
    item = Plan(disk=disk.name, steps=steps)
    return Grow(State.OK, disk.name, part.name, part.fs, item)


def remedy(state: State, fs: FsKind = FsKind.NONE) -> str:
    """判定不通过时的修复建议；token 与建议分离，便于机器判读。"""
    if state is State.NO_DATA:
        return (
            "未发现挂载在 /data 的分区：按 03-installer-design.md §3.6 "
            "的布局创建数据分区后，先 airy-disk grow --dry-run 复核"
        )
    if state is State.NOT_LAST:
        return (
            "数据分区之后还有其它分区，原地扩容会搬动系统分区：备份后"
            "按 single/dual 布局重装，或把 /data 迁到独立磁盘再扩"
        )
    return (
        f"文件系统 {fs.value or '-'} 不支持在线扩容：改用 ext4、xfs 或"
        " btrfs 后重试，或先离线迁移数据"
    )


def repair(step: Step, rc: int) -> str:
    """执行失败时的下一步：缺工具给包名，其余给现场取证入口。"""
    tool = step.argv[0]
    if rc == 127 and tool in PACKAGE:
        return f"缺少 {tool}：安装 {PACKAGE[tool]} 后重试"
    return f"{tool} 退出码 {rc}：{REMOTE}"


def _data(topo: Topology) -> tuple[Disk, Partition] | None:
    for disk in topo.disks:
        for part in disk.parts:
            if part.mount == DATA_MOUNT:
                return disk, part
    return None


def _steps(disk: str, part: Partition) -> tuple[Step, ...]:
    """分区放大一步、文件系统跟上一步；未知文件系统不生成任何步骤。"""
    resize = _resize(part)
    if resize is None:
        return ()
    grow = Step(
        action=Action.GROW,
        target=f"/dev/{disk}",
        argv=(GROWPART, f"/dev/{disk}", str(num(part))),
    )
    return (grow, resize)


def _resize(part: Partition) -> Step | None:
    dev = f"/dev/{part.name}"
    if part.fs is FsKind.EXT4:
        return Step(Action.RESIZE, dev, (RESIZE2FS, dev))
    if part.fs is FsKind.XFS:
        return Step(Action.RESIZE, dev, (XFS_GROWFS, "-d", DATA_MOUNT))
    if part.fs is FsKind.BTRFS:
        return Step(
            Action.RESIZE,
            dev,
            (BTRFS, "filesystem", "resize", "max", DATA_MOUNT),
        )
    return None
