# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 系统与数据分区布局判定
#
# 装机后要回答三件事：/ 与 /data 是否各自挂在独立分区上、数据分区落在
# 哪块盘、这块盘上的数据分区能否就地扩容。三者都从一次探测快照推出，
# 不做任何写操作，装机、升级与门禁因此共用同一份判定（DISK-01）。
#
# 扩容判据只认位置：分区表按号从小往大增长，只有盘上最后一号分区能被
# 原地放大而不移动其余分区。数据分区不是最后一号时，扩容必然搬动系统
# 分区的起止扇区，UUID 与 fsck 结果随之改变，故判 not-last 交由装机
# 现场修正，而不是在运行期「补齐」。
#
# 判据③的四拓扑一致与判据④的无硬编码由探测层的查表实现保证，本模块
# 不按盘名、机型或总线分支；判据②的「系统分区 UUID 与 fsck 不变」是
# 扩容动作的后果，只能由现场取证，这里判定的是其前提。

"""AirymaxOS system/data layout inspection (M-36).

设计依据：0.0.4 方案 §16.4.9 DISK-01 判据①②。:func:`inspect` 是纯
函数，输入一次探测快照，输出布局事实与判定 token；:func:`dump` 供
``airy-disk --json layout`` 与 ``ci/gates/disk-layout.sh`` 直接判读。

"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

from .model import DATA_MOUNT, Disk, Topology
from .probe import host

__all__ = ["DUAL", "NONE", "ROOT", "SINGLE", "Layout", "State", "dump",
           "inspect"]

ROOT = "/"

SINGLE = "single"

DUAL = "dual"

NONE = "none"


@unique
class State(Enum):
    """布局判定；每一项都是门禁可直读的稳定 token。"""

    OK = "ok"
    NO_ROOT = "no-root"
    NO_DATA = "no-data"
    NOT_LAST = "not-last"


@dataclass(frozen=True, slots=True)
class Layout:
    """一次布局判定；``kind`` 为 single/dual，缺任一侧时为 none。

    ``root``/``data`` 是承载对应挂载点的磁盘名：双盘时不同，同盘切分
    时相同。缺任一侧时 ``state`` 指出缺的是哪一侧，``kind`` 不下结论。
    """

    kind: str = NONE
    root: str = ""
    data: str = ""
    state: State = State.NO_ROOT

    @property
    def ok(self) -> bool:
        return self.state is State.OK


def inspect(topo: Topology) -> Layout:
    """判定拓扑中的系统与数据分区布局。

    挂载点取自探测快照的 MOUNTPOINT，与 ``findmnt`` 同源；/ 与 /data
    各自落在哪个分区由挂载点唯一确定，不依赖分区号或盘符。
    """
    root = host(topo, ROOT)
    if root is None:
        return Layout()
    data = host(topo, DATA_MOUNT)
    if data is None:
        return Layout(root=root.name, state=State.NO_DATA)
    kind = SINGLE if data.name == root.name else DUAL
    state = State.OK if _tail(data) else State.NOT_LAST
    return Layout(kind=kind, root=root.name, data=data.name, state=state)


def dump(found: Layout) -> dict[str, object]:
    """序列化判定结果，供 ``airy-disk --json layout`` 输出。"""
    return {
        "kind": found.kind,
        "verdict": found.state.value,
        "root": found.root,
        "data": found.data,
    }


def _tail(disk: Disk) -> bool:
    """数据分区是否盘上最后一号分区，决定其能否原地扩容。"""
    return disk.parts[-1].mount == DATA_MOUNT
