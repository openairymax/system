# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 实时能力矩阵
#
# 一台机器能不能跑实时，不是「装没装实时包」一句话能答的。内核是否
# PREEMPT_RT、实时带宽是否被限制为 0、cgroup 是否记账 RT、调度器是否
# 支持 SCHED_DEADLINE、中断是否线程化、CPU 是否隔离、调频是否被钉在
# performance，这些条件各自独立，缺一条就有一种不同的症状：可能是
# sched_setscheduler 返回 EPERM，可能是延迟尖峰，也可能是频率被省电
# 策略拉走。把它们压成一个布尔值，现场只能看到「实时不可用」，无从下手。
#
# 因此每个条件都单列成一项，并区分 yes / no / unknown：unknown 表示
# 这次探测读不到（无权限或节点不存在），不等于内核不支持——把读不到
# 说成不支持，会让运维去换内核，而实际问题在挂载与权限上。
#
# 判定只读文件与 /proc/cmdline，不写任何接口，也不依赖调试文件系统。
# 需要改的项（调频、隔离）由 X-05 的共存策略负责，探测只负责如实报告
# 现状，避免「探测顺手改了系统」这种不可复现的行为。
#
# 设计依据：docs-linux/170-performance/01-scheduling-performance.md §2.1
# 与 docs-linux/170-performance/07-rt-matrix.md。

"""AirymaxOS real-time capability matrix (X-03)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path

from . import cgroup
from .policy import DEFAULT_CGROUP

__all__ = [
    "DEFAULT_CMDLINE",
    "DEFAULT_PROC",
    "DEFAULT_SYS",
    "GRADE_ADVICE",
    "Cap",
    "Flag",
    "Grade",
    "Matrix",
    "Roots",
    "advice",
    "collect",
    "grade",
]

DEFAULT_SYS = "/sys"

DEFAULT_PROC = "/proc"

DEFAULT_CMDLINE = "/proc/cmdline"

REALTIME = "realtime"

BUDGET = "budget"

GROUP = "group"

DEADLINE = "deadline"

THREADIRQS = "threadirqs"

ISOLATED = "isolated"

GOVERNOR = "governor"

CLOCKSOURCE = "clocksource"

FULL = (REALTIME, BUDGET, GROUP)

GRADE_ADVICE = {
    "ready": "整机满足实时条件：PREEMPT_RT、带宽与组记账均已就绪",
    "soft": "仅有软实时能力：实时类可用但内核不是 PREEMPT_RT",
    "none": "不具备实时能力：实时带宽不可用或未暴露接口",
}


@unique
class Flag(Enum):
    """单项能力的探测结果；unknown 是「这次读不到」而非「不支持」。"""

    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


@unique
class Grade(Enum):
    """整机实时等级；只有 ready 表示完整实时。"""

    READY = "ready"
    SOFT = "soft"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Roots:
    """探测所用的根路径；测试与救援环境可整体改指。"""

    sys: str = DEFAULT_SYS
    proc: str = DEFAULT_PROC
    cgroup: str = DEFAULT_CGROUP
    cmdline: str = DEFAULT_CMDLINE


@dataclass(frozen=True, slots=True)
class Cap:
    """一项能力的探测结论。"""

    name: str
    flag: Flag
    detail: str


@dataclass(frozen=True, slots=True)
class Matrix:
    """一次完整探测的结果；grade 由各项标志汇总而来。"""

    caps: tuple[Cap, ...]
    grade: Grade

    def flag(self, name: str) -> Flag:
        """按名字取某一项标志；未探测的项按 unknown 处理。"""
        for item in self.caps:
            if item.name == name:
                return item.flag
        return Flag.UNKNOWN

    @property
    def pending(self) -> tuple[str, ...]:
        """明确缺失的条件；unknown 不计入，避免把读不到当成不支持。"""
        return tuple(item.name for item in self.caps
                     if item.flag is Flag.NO)

    def dump(self) -> dict[str, object]:
        """命令行 --json 的唯一输出形状。"""
        return {
            "grade": self.grade.value,
            "pending": list(self.pending),
            "caps": [
                {
                    "name": item.name,
                    "flag": item.flag.value,
                    "detail": item.detail,
                }
                for item in self.caps
            ],
        }


def advice(found: Grade) -> str:
    """等级对应的处置建议。"""
    return GRADE_ADVICE[found.value]


def collect(roots: Roots) -> Matrix:
    """按固定顺序探测全部条件，并汇总整机等级。"""
    caps = tuple(probe(roots) for probe in _probes())
    return Matrix(caps, grade(caps))


def grade(caps: tuple[Cap, ...]) -> Grade:
    """完整实时要求三项全 yes；带宽可用即软实时。"""
    flags = {item.name: item.flag for item in caps}
    if all(flags.get(name) is Flag.YES for name in FULL):
        return Grade.READY
    if flags.get(BUDGET) is Flag.YES:
        return Grade.SOFT
    return Grade.NONE


def _probes():
    return (
        _realtime,
        _budget,
        _group,
        _deadline,
        _threadirqs,
        _isolated,
        _governor,
        _clocksource,
    )


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError:
        return None


def _cap(name: str, flag: Flag, detail: str) -> Cap:
    return Cap(name, flag, detail)


def _realtime(roots: Roots) -> Cap:
    text = _text(Path(roots.sys) / "kernel" / "realtime")
    if text is None:
        return _cap(REALTIME, Flag.NO, "sys/kernel/realtime 不存在")
    if text == "1":
        return _cap(REALTIME, Flag.YES, "PREEMPT_RT")
    return _cap(REALTIME, Flag.NO, f"realtime={text}")


def _budget(roots: Roots) -> Cap:
    text = _text(Path(roots.proc) / "sys" / "kernel" / "sched_rt_runtime_us")
    if text is None:
        return _cap(BUDGET, Flag.UNKNOWN, "sched_rt_runtime_us 不可读")
    try:
        value = int(text, 10)
    except ValueError:
        return _cap(BUDGET, Flag.UNKNOWN, f"sched_rt_runtime_us={text}")
    if value == 0:
        return _cap(BUDGET, Flag.NO, "runtime=0 禁止实时类")
    return _cap(BUDGET, Flag.YES, f"runtime={value}")


def _group(roots: Roots) -> Cap:
    found = cgroup.detect(roots.cgroup)
    if found is None:
        return _cap(GROUP, Flag.NO, "未找到 cgroup cpu 记账树")
    if found.supported:
        return _cap(GROUP, Flag.YES, f"{found.kind.value}:{found.file}")
    if found.routed:
        return _cap(GROUP, Flag.NO, "内核未启用 RT 组调度：仅全局预算限流")
    return _cap(GROUP, Flag.NO, "统一树未暴露 cpu 控制器")


def _deadline(roots: Roots) -> Cap:
    path = Path(roots.proc) / "sys" / "kernel" / "sched_deadline_period_max_us"
    if path.is_file():
        return _cap(DEADLINE, Flag.YES, "SCHED_DEADLINE 可用")
    return _cap(DEADLINE, Flag.NO, "无 sched_deadline 接口")


def _threadirqs(roots: Roots) -> Cap:
    return _cmdline_flag(
        roots, THREADIRQS, lambda token: token == THREADIRQS
    )


def _isolated(roots: Roots) -> Cap:
    return _cmdline_flag(
        roots, ISOLATED, lambda token: token.startswith(
            ("isolcpus=", "nohz_full=")
        )
    )


def _cmdline_flag(roots: Roots, name: str, match) -> Cap:
    text = _text(Path(roots.cmdline))
    if text is None:
        return _cap(name, Flag.UNKNOWN, "cmdline 不可读")
    if any(match(token) for token in text.split()):
        return _cap(name, Flag.YES, "cmdline 已设置")
    return _cap(name, Flag.NO, "cmdline 未设置")


def _governor(roots: Roots) -> Cap:
    path = (Path(roots.sys) / "devices" / "system" / "cpu" / "cpu0"
            / "cpufreq" / "scaling_governor")
    text = _text(path)
    if text is None:
        return _cap(GOVERNOR, Flag.UNKNOWN, "无 cpufreq")
    if text == "performance":
        return _cap(GOVERNOR, Flag.YES, "performance")
    return _cap(GOVERNOR, Flag.NO, text)


def _clocksource(roots: Roots) -> Cap:
    path = (Path(roots.sys) / "devices" / "system" / "clocksource"
            / "clocksource0" / "current_clocksource")
    text = _text(path)
    if text is None:
        return _cap(CLOCKSOURCE, Flag.UNKNOWN, "无 clocksource")
    if text == "tsc":
        return _cap(CLOCKSOURCE, Flag.YES, "tsc")
    return _cap(CLOCKSOURCE, Flag.NO, text)
