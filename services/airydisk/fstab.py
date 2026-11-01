# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS fstab 判定
#
# 布局判定只看此刻挂在哪里，看不到重启之后还挂不挂得上。真实故障是：
# 安装器按设备名写 /data，用户扩容后内核改号，数据分区开机即失挂，而
# 系统本身照常起来——故障延后到用户发现文件不见时才暴露。因此这里把
# 已安装系统的 fstab 当成证据来判：系统与数据两条挂载记录必须在、必须
# 用稳定标识（UUID/LABEL/PARTUUID），数据分区不得 noauto。

"""AirymaxOS fstab verdict (M-36).

设计依据：0.0.4 方案 §6.3 M-36 与 DISK-01 判据②（扩容后系统分区
UUID 与 fsck 结果不变）。判定只读一份 ``/etc/fstab`` 文本，token 与
:mod:`airydisk.layout` 同形，供 ``airy-disk layout --fstab`` 与
``ci/gates/disk-layout.sh`` 直接判读。

"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

from .model import DATA_MOUNT

__all__ = ["ROOT", "Persist", "State", "check", "dump"]

ROOT = "/"

STABLE = ("UUID=", "LABEL=", "PARTUUID=")

NOAUTO = "noauto"


@unique
class State(Enum):
    """fstab 判定结论；除 ``OK`` 外都意味着重启后布局不成立。"""

    OK = "ok"
    NO_ROOT = "no-root"
    NO_DATA = "no-data"
    UNSTABLE = "unstable"
    NOAUTO = "noauto"


@dataclass(frozen=True, slots=True)
class Persist:
    """一份 fstab 的判定结果。"""

    state: State

    @property
    def ok(self) -> bool:
        return self.state is State.OK


@dataclass(frozen=True, slots=True)
class Entry:
    """fstab 的一行有效记录，只留判定需要的列。"""

    source: str
    mount: str
    opts: tuple[str, ...]

    @property
    def stable(self) -> bool:
        """源是否用稳定标识；设备名与 /dev 路径会随枚举顺序变化。"""
        return self.source.startswith(STABLE)


def parse(text: str) -> tuple[Entry, ...]:
    """按空白分列解析；注释与不足三列的行一律跳过。"""
    rows = []
    for line in text.splitlines():
        body = line.split("#", 1)[0].split()
        if len(body) < 3:
            continue
        opts = () if len(body) < 4 else tuple(body[3].split(","))
        rows.append(Entry(body[0], body[1], opts))
    return tuple(rows)


def check(text: str) -> Persist:
    """判定 fstab 是否满足系统与数据分区独立、可持久挂载。"""
    rows = parse(text)
    root = _find(rows, ROOT)
    if root is None:
        return Persist(State.NO_ROOT)
    data = _find(rows, DATA_MOUNT)
    if data is None:
        return Persist(State.NO_DATA)
    if not root.stable or not data.stable:
        return Persist(State.UNSTABLE)
    if NOAUTO in data.opts:
        return Persist(State.NOAUTO)
    return Persist(State.OK)


def dump(found: Persist) -> dict[str, object]:
    """序列化为 JSON 可编码的字典，键集与 layout.dump 的判定部分同形。"""
    return {"persist": found.state.value}


def _find(rows: tuple[Entry, ...], mount: str) -> Entry | None:
    return next((row for row in rows if row.mount == mount), None)
