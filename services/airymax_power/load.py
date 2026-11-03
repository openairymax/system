# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 整机负载采样与分级
#
# 285H 现场：两个本地模型常驻（28.8G RSS / 30G 内存），swap 已用 5.4G，
# load 21.9，实时延迟从 98µs 跳到 8125µs，随后断连、卡死、重启。此前的
# 档位只按供电与温度选：温度不高、电量充足，系统就认为机器很健康，把档位
# 钉在最省电一侧；而此刻真正拖垮机器的是内存回收与 CPU 排队，这两件事在
# 温度计上完全看不出来。
#
# 因此把负载作为独立输入。优先读 PSI（/proc/pressure）的 some avg10，它直接
# 给出「有任务因等 CPU、等内存、等 IO 而停住」的时间占比，是这条故障链最
# 贴近的度量；内核没有 PSI 时回退到 loadavg 与 /proc/meminfo。两条路径产出
# 同一组字段，判定按来源选用对应量纲的门限，来源本身只作为证据写进报告——
# 不能出现「有 PSI 的机器降档、没有 PSI 的机器不降档」这类现场分叉。
#
# swap 单独计分：285H 故障时刻内存并未耗尽（MemAvailable 仍有 14G），但
# swap 已用 34%，回收路径长期活跃，这才是延迟抖动的来源。只看 MemAvailable
# 会漏掉它。没有配置 swap 的机器 swap 记为 0，而不是「未知」，否则服务器
# 档位会因为缺省 swap 而永远报压力。

"""AirymaxOS whole-machine load sampling."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import notify, sysfs
from .policy import Policy
from .thermal import Severity, peak

__all__ = ["Load", "alert", "sample"]

PSI_CPU = "cpu"

PSI_MEMORY = "memory"

PSI_IO = "io"

STAT = "some"

AVG = "avg10"

PSI_ORIGIN = "psi"

PROC_ORIGIN = "proc"

PSI_CPU_WARN = 50.0

PSI_CPU_HOT = 80.0

PSI_MEM_WARN = 10.0

PSI_MEM_HOT = 50.0

LOAD_WARN = 100.0

LOAD_HOT = 150.0

USE_WARN = 75.0

USE_HOT = 90.0

IO_WARN = 20.0

IO_HOT = 50.0

SWAP_WARN = 20.0

SWAP_HOT = 50.0

PRESSED = "hot"

RELEASED = "ok"

MESSAGE = ("AirymaxOS 内存压力升高（回收停顿 %s，swap 已用 %s）；"
           "本地模型长任务可能把整机拖入交换抖动，"
           "请减少常驻模型或扩容内存")


@dataclass(frozen=True, slots=True)
class Load:
    """一次负载采样。

    origin 决定各字段的量纲：psi 下 cpu/memory/io 是 some avg10 的百分比，
    proc 下 cpu 是 1 分钟负载按核数归一后的百分比、memory 是已用占比，io
    不可测因此为 None。swap 两路同义，都是已用占比。
    """

    origin: str
    cpu: float | None
    memory: float | None
    io: float | None
    swap: float | None

    @property
    def strain(self) -> Severity:
        return peak(_cpu(self), _memory(self), _io(self), _swap(self.swap))

    def dump(self) -> dict[str, object]:
        return {
            "origin": self.origin,
            "cpu": _round(self.cpu),
            "memory": _round(self.memory),
            "io": _round(self.io),
            "swap": _round(self.swap),
            "strain": self.strain.value,
        }


def sample(policy: Policy) -> Load:
    """采样负载；PSI 目录缺失或三条全不可读时回退到 proc。"""
    swap = _swap_use(Path(policy.meminfo))
    found = _pressures(Path(policy.psi))
    if found is not None:
        return Load(PSI_ORIGIN, *found, swap)
    return Load(PROC_ORIGIN, _cores_load(policy),
                _usage(Path(policy.meminfo)), None, swap)


def grade(value: float | None, warn: float, hot: float) -> Severity:
    """按给定门限分级；没有读数时为 UNKNOWN，不参与取最严重。"""
    if value is None:
        return Severity.UNKNOWN
    if value >= hot:
        return Severity.HOT
    if value >= warn:
        return Severity.WARN
    return Severity.OK


def alert(policy: Policy, work: Load) -> None:
    """内存压力到 hot 时广播一次；回落后复位，下一次升高再报。"""
    pressed = _memory(work).rank >= Severity.HOT.rank
    state = PRESSED if pressed else RELEASED
    if notify.last(policy.press) == state:
        return
    notify.store(policy.press, state)
    if pressed:
        notify.call(policy.wall,
                    (MESSAGE % (_text(work.memory), _text(work.swap)),))


def _cpu(work: Load) -> Severity:
    if work.origin == PSI_ORIGIN:
        return grade(work.cpu, PSI_CPU_WARN, PSI_CPU_HOT)
    return grade(work.cpu, LOAD_WARN, LOAD_HOT)


def _memory(work: Load) -> Severity:
    if work.origin == PSI_ORIGIN:
        return grade(work.memory, PSI_MEM_WARN, PSI_MEM_HOT)
    return grade(work.memory, USE_WARN, USE_HOT)


def _io(work: Load) -> Severity:
    return grade(work.io, IO_WARN, IO_HOT)


def _swap(value: float | None) -> Severity:
    return grade(value, SWAP_WARN, SWAP_HOT)


def _pressures(root: Path) -> tuple[float | None, ...] | None:
    """读 CPU/内存/IO 三条 PSI；全部不可读时返回 None 交给回退路径。"""
    found = tuple(_psi(root / name) for name in (PSI_CPU, PSI_MEMORY, PSI_IO))
    return None if all(item is None for item in found) else found


def _psi(path: Path) -> float | None:
    """取 some 行的 avg10；没有 some 行或值非法时返回 None。"""
    text = sysfs.read_text(path)
    if text is None:
        return None
    for line in text.splitlines():
        fields = line.split()
        if fields and fields[0] == STAT:
            return _avg(fields[1:])
    return None


def _avg(fields: list[str]) -> float | None:
    for field in fields:
        key, _, value = field.partition("=")
        if key != AVG:
            continue
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _cores_load(policy: Policy) -> float | None:
    """1 分钟负载按可见核数归一；核数不可知时返回 None。"""
    text = sysfs.read_text(Path(policy.loadavg))
    if text is None:
        return None
    try:
        load = float(text.split()[0])
    except (IndexError, ValueError):
        return None
    cores = _cores(Path(policy.cpus))
    if not cores:
        return None
    return load * 100 / cores


def _cores(root: Path) -> int:
    try:
        return sum(1 for entry in root.glob("cpu[0-9]*")
                   if entry.name[3:].isdigit())
    except OSError:
        return 0


def _usage(path: Path) -> float | None:
    """内存已用占比 = (MemTotal - MemAvailable) / MemTotal。"""
    info = _info(path)
    total = info.get("MemTotal")
    free = info.get("MemAvailable")
    if not total or free is None:
        return None
    return (total - free) * 100 / total


def _swap_use(path: Path) -> float | None:
    """swap 已用占比；未配置 swap 记 0，读不到内存信息记 None。"""
    info = _info(path)
    total = info.get("SwapTotal")
    free = info.get("SwapFree")
    if total is None or free is None:
        return None
    if total == 0:
        return 0.0
    return (total - free) * 100 / total


def _info(path: Path) -> dict[str, int]:
    text = sysfs.read_text(path)
    if text is None:
        return {}
    found = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        fields = rest.split()
        if not fields:
            continue
        try:
            found[key] = int(fields[0])
        except ValueError:
            continue
    return found


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _text(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}%"
