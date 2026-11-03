# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 新实例的准入判定
#
# 换页上限只保证「越线时杀掉越线的那一个」，不保证「不越线」。285H 现场
# 三个推理实例的 RSS 合计 29.5G，物理内存 30G，swap 已用 11G，同一个窗口
# 里出现 1042/1855 µs 的越线，而 allocstall 与 pgscan_direct 一次都没有
# 增长——抢占来自换页写回，只要用量长期贴着物理内存就必然发生。靠延迟
# 参数消除不了，唯一有效的收口是在启动前拒绝装不下的新实例。
#
# 已在跑的实例一个都不动：准入只决定「要不要再开一个」，不做驱逐。驱逐
# 会让用户正在跑的长任务凭空消失，比拒绝启动更糟；而且判断谁该被驱逐需要
# 业务语义，操作系统侧没有这份信息。
#
# 判据只有一条：MemAvailable 减去保留量。保留量是物理内存减去 agent 侧
# 上限，上限读自 slice 单元，与 memcap 同一份声明，不另立百分比。可用内存
# 偏乐观是内核估算的固有性质，这里不再叠一层推演——真到了算错的那一步，
# memsw 上限只会杀掉越线实例，不会拖住整机。
#
# 拒绝的语义是给出缺口字节数并以退出码 3 结束，调用方据此提示用户；不
# 降级、不排队、不假装成功。设计依据：docs-linux/170-performance/
# 07-rt-matrix.md §5.5。

"""AirymaxOS admission control for new inference instances (X-05)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import memcap

__all__ = [
    "DEFAULT_MEMINFO",
    "DEFAULT_UNITDIR",
    "FIT",
    "REFUSE",
    "UNKNOWN",
    "Verdict",
    "advice",
    "check",
    "reserve",
    "size",
]

DEFAULT_MEMINFO = memcap.DEFAULT_MEMINFO

DEFAULT_UNITDIR = memcap.DEFAULT_UNITDIR

FIT = "fit"

REFUSE = "refuse"

UNKNOWN = "unknown"

KEYS = ("MemTotal", "MemAvailable", "SwapFree")

UNITS = {
    "B": 1,
    "K": 1 << 10,
    "KB": 1 << 10,
    "M": 1 << 20,
    "MB": 1 << 20,
    "G": 1 << 30,
    "GB": 1 << 30,
    "T": 1 << 40,
    "TB": 1 << 40,
}

ADVICE = {
    FIT: "可以启动：装得下，已在跑的实例不受影响",
    REFUSE: "拒绝启动：缺口见 short，先停一个实例或减少 slot",
    UNKNOWN: "无法判定：内存接口不可读或未给出 --need",
}


@dataclass(slots=True)
class Verdict:
    """一次准入判定的全部输入与结论。"""

    state: str
    need: int
    free: int
    total: int
    avail: int
    swap: int
    keep: int
    note: str = ""

    @property
    def short(self) -> int:
        """缺口字节数；装得下时为 0。"""
        return max(0, self.need - self.free)

    def dump(self) -> dict[str, object]:
        return {
            "state": self.state,
            "need": self.need,
            "free": self.free,
            "total": self.total,
            "avail": self.avail,
            "swap": self.swap,
            "reserve": self.keep,
            "short": self.short,
            "note": self.note,
        }


def advice(state: str) -> str:
    """把结论换成一行动作，输出层不自行编造处置口径。"""
    return ADVICE.get(state, ADVICE[UNKNOWN])


def size(text: str) -> int:
    """把 ``12G`` / ``512M`` / ``1GiB`` / 纯字节换成字节数。

    Returns:
        字节数；无法判读时返回 0，由调用方按用法错误处理。
    """
    item = text.strip().upper().replace(" ", "")
    if item.endswith("IB"):
        item = item[:-2] + "B"
    index = 0
    while index < len(item) and item[index].isdigit():
        index += 1
    head, tail = item[:index], item[index:]
    if not head:
        return 0
    factor = UNITS.get(tail or "B")
    if factor is None:
        return 0
    return int(head, 10) * factor


def _read(path: str) -> dict[str, int]:
    """读三项内存计数；单位是 kB，换算成字节。

    只取判定要用的三行，整份 meminfo 里其余字段与准入无关，读进来只会
    让「判据是什么」变得模糊。
    """
    try:
        text = Path(path).read_text(encoding="ascii")
    except OSError:
        return {}
    found: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        if name not in KEYS:
            continue
        fields = rest.split()
        try:
            found[name] = int(fields[0], 10) * 1024
        except (IndexError, ValueError):
            continue
    return found


def reserve(unitdir: str, meminfo: str, total: int) -> int:
    """保留量：物理内存减去 agent 侧上限，不被新实例占用。

    没有任何声明时按一成保留。缺单元文件的宿主仍要能判定，而「不保留」
    会让第一个实例就把整机吃满，比保守更危险。
    """
    found = [item.mem for item in memcap.bounds(unitdir, meminfo)]
    if not found:
        return total // 10
    keep = total - max(found)
    return keep if keep > 0 else 0


def check(
    need: int,
    meminfo: str = DEFAULT_MEMINFO,
    unitdir: str = DEFAULT_UNITDIR,
) -> Verdict:
    """按「可用内存减保留量」判定新实例能否装入。

    Args:
        need: 新实例预计占用的字节数。
        meminfo: meminfo 路径，测试注入用。
        unitdir: 单元目录，保留量从这里读，与 memcap 同源。

    Returns:
        判定结果；输入不可判读时为 ``UNKNOWN``，不冒充可装。
    """
    values = _read(meminfo)
    total = values.get("MemTotal", 0)
    avail = values.get("MemAvailable", 0)
    swap = values.get("SwapFree", 0)
    keep = reserve(unitdir, meminfo, total) if total > 0 else 0
    free = avail - keep
    if free < 0:
        free = 0
    if total <= 0 or need <= 0:
        return Verdict(
            UNKNOWN, need, free, total, avail, swap, keep,
            "内存接口不可读或未给出需求",
        )
    if need <= free:
        return Verdict(FIT, need, free, total, avail, swap, keep)
    note = f"还差 {need - free} 字节；换页余量不计入判据"
    return Verdict(REFUSE, need, free, total, avail, swap, keep, note)
