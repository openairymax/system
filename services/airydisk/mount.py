# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 数据分区挂载恢复
#
# 开机后 /data 没挂上的后果是隐性的：目录还在，写进去的文件全部落在根
# 分区，直到根分区被写满才暴露。本模块在 agentrt 拉起之前把 /data 挂上，
# 只在确实没挂载时执行一次 mount，已挂载时不碰任何东西。
#
# 拒绝比「尽量挂上」更重要：fstab 用设备名记录 /data 时，内核一旦改号，
# 同一条 mount 命令挂上的可能是别人的文件系统，静默写坏数据；带 noauto
# 的记录则是运维明确要求不自动挂载。两种现场都判拒绝并给出改正路径，
# 由单元失败暴露问题，而不是在运行期替用户猜意图。
#
# 判据④：拒绝与失败的下一步都写成可执行动作（blkid 取 UUID 改写 fstab、
# 去掉 noauto、mount -a 复核），不只回一个退出码。

"""AirymaxOS /data mount recovery (K-24).

设计依据：docs-linux/190-distribution/03-installer-design.md §3.6 与
0.0.4 方案 §6.3 K-24。:func:`judge` 只读 fstab 文本与挂载事实，
:func:`ensure` 在其判定为待挂载时执行一次 mount。

"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from os.path import ismount

from .fstab import NOAUTO, Entry, parse
from .model import DATA_MOUNT
from .ops import Action, Step
from .runner import TIMEOUT, Runner, run

__all__ = [
    "MOUNT",
    "Mount",
    "State",
    "dump",
    "ensure",
    "judge",
    "planned",
    "remedy",
]

MOUNT = "/usr/bin/mount"


@unique
class State(Enum):
    """挂载判定；除 ``OK`` 与 ``RESTORED`` 外都不代表 /data 可用。"""

    MOUNTED = "mounted"
    READY = "ready"
    RESTORED = "restored"
    NO_ENTRY = "no-entry"
    UNSTABLE = "unstable"
    NOAUTO = "noauto"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Mount:
    """一次挂载判定；``step`` 非空表示还需要执行一次 mount。"""

    state: State
    source: str = ""
    rc: int = 0
    advice: str = ""
    step: Step | None = None

    @property
    def ok(self) -> bool:
        return self.state in (State.MOUNTED, State.RESTORED)


def judge(text: str, mounted: bool) -> Mount:
    """判定 fstab 能否把 /data 挂上；不执行任何外部命令。"""
    if mounted:
        return Mount(State.MOUNTED)
    row = _row(text)
    if row is None:
        return Mount(State.NO_ENTRY, advice=remedy(State.NO_ENTRY))
    if NOAUTO in row.opts:
        return Mount(State.NOAUTO, row.source, advice=remedy(State.NOAUTO))
    if not row.stable:
        return Mount(State.UNSTABLE, row.source,
                     advice=remedy(State.UNSTABLE))
    step = Step(Action.MOUNT, DATA_MOUNT, (MOUNT, DATA_MOUNT))
    return Mount(State.READY, row.source, step=step)


def planned(text: str) -> Mount:
    """只判定不执行；--dry-run 与 :func:`ensure` 共用同一条判定路径。"""
    return judge(text, ismount(DATA_MOUNT))


def ensure(text: str, runner: Runner = run,
           timeout: float = TIMEOUT) -> Mount:
    """判定之后按需挂载 /data；命令起不来或非零退出都判失败。"""
    found = planned(text)
    if found.step is None:
        return found
    result = runner(found.step, timeout)
    if result.ok:
        return Mount(State.RESTORED, found.source, step=found.step)
    return Mount(State.FAILED, found.source, result.rc,
                 remedy(State.FAILED, result.rc), found.step)


def remedy(state: State, rc: int = 0) -> str:
    """拒绝或失败时的下一步；只描述动作，不改动任何文件。"""
    if state is State.NO_ENTRY:
        return (
            "fstab 缺少 /data 记录：用 blkid 取数据分区 UUID，按 "
            "03-installer-design.md §3.6 补一条记录后重试"
        )
    if state is State.NOAUTO:
        return (
            "fstab 的 /data 记录带 noauto：去掉该选项，否则开机与手动"
            "都不会挂载数据分区"
        )
    if state is State.UNSTABLE:
        return (
            "fstab 的 /data 用设备名记录：内核改号后会挂错文件系统，"
            "用 blkid 取 UUID 改写该行后重试"
        )
    return (
        f"mount 退出码 {rc}：用 lsblk 确认设备存在，执行 mount -a 复核，"
        "并查看 dmesg 中的文件系统错误"
    )


def dump(found: Mount) -> dict[str, object]:
    """序列化判定结果，供 ``airy-disk --json mount`` 输出。"""
    return {
        "verdict": found.state.value,
        "source": found.source,
        "rc": found.rc,
        "advice": found.advice,
    }


def _row(text: str) -> Entry | None:
    return next((row for row in parse(text) if row.mount == DATA_MOUNT),
                None)
