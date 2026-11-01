# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 磁盘操作面契约
#
# 操作层只回答两件事：要做什么（Plan/Step），以及能不能做（护栏）。
# 计划是纯数据，不含执行状态，因此可以离线复算、逐条打印并在执行前
# 交给用户二次确认；护栏把不可改写的盘挡在计划生成之前，拒绝理由以
# 稳定 token 输出，避免上层用自由文本判断（M-31、M4-4）。

"""AirymaxOS disk operation contract (M-31-01).

设计依据：docs-linux/190-distribution/03-installer-design.md §3 与
0.0.4 方案 §6.3 M-31，达成判据见同文档 M4-4「破坏性操作需二次确认」。
本切片只定义计划类型与护栏判定，不执行任何外部命令：执行与回滚在
后续切片补入，判定保持纯函数以便离线复算。

"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

from .model import Disk

__all__ = [
    "SCHEMA_VERSION",
    "Action",
    "Plan",
    "REFUSAL_TEXT",
    "Refusal",
    "Step",
    "refuse",
]

SCHEMA_VERSION = 1


@unique
class Action(Enum):
    """计划中的单个动作，token 与 ``airy-disk --json`` 输出一致。"""

    WIPE = "wipe"
    PARTITION = "partition"
    FORMAT = "format"
    MOUNT = "mount"


@unique
class Refusal(Enum):
    """拒绝改写磁盘的理由；每一项都是不可绕过、不可合并的护栏。"""

    READ_ONLY = "read-only"
    REMOVABLE = "removable"
    HOSTS_SYSTEM = "hosts-system"


REFUSAL_TEXT = {
    Refusal.READ_ONLY: "磁盘为只读，无法写入分区表",
    Refusal.REMOVABLE: "磁盘为可移动介质，不作为改写目标",
    Refusal.HOSTS_SYSTEM: "磁盘承载正在运行的系统，拒绝改写",
}


@dataclass(frozen=True, slots=True)
class Step:
    """单个待执行动作；``argv`` 是完整命令行，不经过 shell。

    ``undo`` 为空表示无需回滚；非空时按计划的反向顺序执行即可撤销。
    """

    action: Action
    target: str
    argv: tuple[str, ...]
    undo: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.target or not self.argv:
            raise ValueError("step needs target and argv")


@dataclass(frozen=True, slots=True)
class Plan:
    """针对单块磁盘的完整计划；步骤按给定顺序执行，回滚反向。"""

    disk: str
    steps: tuple[Step, ...] = ()
    schema: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.disk:
            raise ValueError("plan needs a disk")

    @property
    def empty(self) -> bool:
        """无动作的计划是合法结果：磁盘已是目标布局。"""
        return not self.steps


def refuse(disk: Disk, host: str | None) -> Refusal | None:
    """判定磁盘能否作为改写目标，None 表示放行。

    ``host`` 是承载运行系统的磁盘名，由 :func:`airydisk.probe.host`
    取得；None 表示安装介质里 "/" 没有承载盘，此时不额外拦截。
    """
    if disk.ro:
        return Refusal.READ_ONLY
    if disk.removable:
        return Refusal.REMOVABLE
    if disk.name == host:
        return Refusal.HOSTS_SYSTEM
    return None
