# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 安装期选盘记录
#
# 安装器 %pre 在分区之前把选中的磁盘、容量、总线、固件与布局写成
# /var/lib/airymaxos/install-disk.json（distro/kickstart/disk-select.sh
# 的 airy_rec）。装机后要回答「现在这块盘还是装机时那块吗」，只能拿
# 同一份记录与现场探测逐字段比对。记录缺失、损坏与不一致是三种不同的
# 事实，判定因此分成独立 token，不合并成一个「失败」。

"""AirymaxOS install-time disk record (M-31).

设计依据：0.0.4 方案 §1.1 判据③「装机后 ``airy-disk status`` 与安装器
``%pre`` 的选盘记录一致」。记录由安装器写、由本模块读，schema 由写入方
单独定义，这里只做解析与比对，不复制选盘规则。

比对只看安装期确实掌握、系统运行期仍可观测的三个字段：盘名、容量与
总线。固件与布局随记录输出，供人工判读，不参与判定——同一块盘可以在
BIOS 与 UEFI 之间切换而无需判负。

"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum, unique

from .model import Topology

__all__ = ["PATH", "SCHEMA", "Agree", "Install", "Verdict", "compare",
           "dump", "load"]

PATH = "/var/lib/airymaxos/install-disk.json"

SCHEMA = 1

# 安装器的总线列在 TRAN 为空时写 "-"：virtio 与 loop 没有传输层，
# 记录里给的是「未知」而不是某个具体总线，比对时必须跳过。
UNKNOWN_BUS = "-"


@unique
class Verdict(Enum):
    """记录与现场的关系；每一项都是可直接判读的稳定 token。"""

    MATCH = "match"
    MISMATCH = "mismatch"
    ABSENT = "absent"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class Install:
    """安装期选盘记录；``state`` 为 absent/invalid 时其余字段为空。"""

    state: str
    disk: str = ""
    size: int = 0
    bus: str = ""
    firmware: str = ""
    layout: str = ""
    medium: str = ""

    @property
    def known(self) -> bool:
        return self.state == "ok"


@dataclass(frozen=True, slots=True)
class Agree:
    """一次比对的结果；``diff`` 按字段名列出不一致项。"""

    verdict: Verdict
    diff: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.verdict is Verdict.MATCH


def load(path: str | None = None) -> Install:
    """读取记录；文件缺失与内容不可用分别落 absent 与 invalid。"""
    try:
        with open(path or PATH, encoding="utf-8") as handle:
            obj = json.load(handle)
    except FileNotFoundError:
        return Install(state="absent")
    except (OSError, ValueError):
        return Install(state="invalid")
    if not isinstance(obj, dict) or obj.get("schema") != SCHEMA:
        return Install(state="invalid")
    return Install(
        state="ok",
        disk=_text(obj.get("disk")),
        size=_int(obj.get("bytes")),
        bus=_text(obj.get("bus")),
        firmware=_text(obj.get("firmware")),
        layout=_text(obj.get("layout")),
        medium=_text(obj.get("medium")),
    )


def compare(install: Install, topo: Topology) -> Agree:
    """把记录与现场拓扑逐字段比对。

    记录说哪块盘，就在拓扑里找哪块盘：找不到即 disk，容量或总线不符
    逐项记入 diff。容量是分区表的直接结果，被换盘、被扩缩都会在这里
    暴露；总线只在双方都已知时比较。
    """
    if not install.known:
        return Agree(Verdict.ABSENT if install.state == "absent"
                     else Verdict.INVALID)
    disk = next((item for item in topo.disks if item.name == install.disk),
                None)
    if disk is None:
        return Agree(Verdict.MISMATCH, ("disk",))
    diff: list[str] = []
    if disk.size != install.size:
        diff.append("size")
    if install.bus != UNKNOWN_BUS and disk.bus.value != install.bus:
        diff.append("bus")
    if not diff:
        return Agree(Verdict.MATCH)
    return Agree(Verdict.MISMATCH, tuple(diff))


def dump(install: Install, agree: Agree) -> dict[str, object]:
    """序列化比对结果，供 ``airy-disk --json status`` 输出。"""
    return {
        "state": install.state,
        "verdict": agree.verdict.value,
        "diff": list(agree.diff),
        "disk": install.disk,
        "bytes": install.size,
        "bus": install.bus,
        "firmware": install.firmware,
        "layout": install.layout,
        "medium": install.medium,
    }


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
