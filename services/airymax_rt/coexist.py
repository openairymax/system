# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 桌面与实时负载的共存策略
#
# 实时负载与桌面不是二选一。实时带宽由 X-01 限住总量，但带宽只约束
# SCHED_FIFO/RR 的占比，不约束 agent 侧的重 CFS 负载：合成器与输入线程
# 仍是 SCHED_OTHER，和 agent 进程在同一个 CPU 上按权重分配。285H 上的
# 卡顿现场正是这样来的——实时预算没超，CPU 全被普通 agent 抢占。
#
# 因此共存动作只有一个：桌面 slice 的 CPUWeight 在基线与保护值之间切换。
# 触发信号首选 PSI 的 CPU some avg10——它度量「有任务可运行却在等 CPU」
# 的时间占比，正是桌面失响应的直接指标。
#
# 285H 的 6.6.157-alk 没有编入 PSI，策略在唯一可验收的机器上因此恒为基线，
# 等于没装。回退信号取 1 分钟负载均值除以核数，单位同样是「占满整机的比例」，
# 但语义不同：loadavg 把不可中断睡眠也算进来，等 IO 时会虚高。两者因此不能
# 共用一个阈值——PSI 用 20% 的有任务等 CPU 时间占比，负载回退要求整机平均
# 可运行任务数达到核数（100%）才抬权重，把 IO 等待的虚高吸收掉。两个都读
# 不到时才保持基线并如实说明，而不是把「没有读数」当成「没有争用」。
#
# 矩阵等级是准入而不是动作：不具备实时能力时，实时负载本来就不会被调度，
# 桌面也不需要为它让路；等级与 governor/isolated/threadirqs 仍作为建议
# 输出，便于现场定位「为什么实时延迟降不下来」。调频由 airy-power 独占，
# 本模块只读不写——两处同时写 scaling_governor 会互相覆盖。
#
# 落地走 systemctl set-property --runtime：只在本次启动内生效，基线仍由
# airymax-desktop.slice 单元钉住，机器重启后不会残留一个没有守护进程解释
# 的权重。写入后回读 CPUWeight，只有回读值与目标一致才算落地。
#
# 设计依据：docs-linux/170-performance/07-rt-matrix.md、0.0.4 方案 §6.7
# X-05。

"""AirymaxOS desktop and real-time coexistence (X-05)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path

from . import matrix

__all__ = [
    "BASE_WEIGHT",
    "DEFAULT_DESKTOP",
    "DEFAULT_LOADAVG",
    "DEFAULT_PRESSURE",
    "DEFAULT_SYSTEMCTL",
    "LIMIT",
    "LOAD_LIMIT",
    "PROTECT_WEIGHT",
    "Coexist",
    "Mode",
    "Plan",
    "Reading",
    "Source",
    "advice",
    "apply",
    "decide",
    "inspect",
]

DEFAULT_SYSTEMCTL = "/usr/bin/systemctl"

DEFAULT_DESKTOP = "airymax-desktop.slice"

DEFAULT_PRESSURE = "/proc/pressure/cpu"

DEFAULT_LOADAVG = "/proc/loadavg"

CPUINFO = "cpuinfo"

BASE_WEIGHT = 50

PROTECT_WEIGHT = 200

LIMIT = 20.0

LOAD_LIMIT = 100.0

MODE_ADVICE = {
    "absent": "桌面 slice 未加载：按无桌面处理，不调整权重",
    "failed": "systemctl 查询失败：未做任何调整",
    "normal": "CPU 争用未达阈值：桌面保持基线权重 %d" % BASE_WEIGHT,
    "protect": "CPU 争用超过 %.0f%%：桌面权重提升到 %d" % (
        LIMIT, PROTECT_WEIGHT
    ),
}


@unique
class Mode(Enum):
    """共存动作的四种结果；absent 与 failed 是两种不同的「没动」。"""

    ABSENT = "absent"
    FAILED = "failed"
    NORMAL = "normal"
    PROTECT = "protect"


@unique
class Source(Enum):
    """争用读数的来源；none 表示两条路径都读不到。"""

    PSI = "psi"
    LOAD = "loadavg"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Reading:
    """一次争用采样；百分比含义随来源变化，故与来源同时传递。"""

    value: float | None
    source: Source = Source.NONE
    cores: int | None = None


@dataclass(frozen=True, slots=True)
class Coexist:
    """一次评估的输入；单元名与路径可替换，便于测试与救援环境。"""

    unit: str = DEFAULT_DESKTOP
    systemctl: str = DEFAULT_SYSTEMCTL
    pressure: str = DEFAULT_PRESSURE
    loadavg: str = DEFAULT_LOADAVG
    limit: float = LIMIT
    load_limit: float = LOAD_LIMIT
    roots: matrix.Roots = matrix.Roots()


@dataclass(frozen=True, slots=True)
class Plan:
    """一次评估的结论；applied 表示运行时权重已等于目标值。"""

    mode: Mode
    unit: str
    weight: int
    current: int | None
    pressure: float | None
    grade: matrix.Grade
    present: bool
    applied: bool
    notes: tuple[str, ...] = ()
    detail: str = ""
    origin: str = Source.NONE.value
    cores: int | None = None

    @property
    def admit(self) -> bool:
        """是否具备实时能力；不具备时实时负载不会被调度。"""
        return self.grade is not matrix.Grade.NONE

    def dump(self) -> dict[str, object]:
        """命令行 --json 的唯一输出形状。"""
        return {
            "mode": self.mode.value,
            "unit": self.unit,
            "weight": self.weight,
            "current": self.current,
            "pressure": self.pressure,
            "origin": self.origin,
            "cores": self.cores,
            "grade": self.grade.value,
            "present": self.present,
            "admit": self.admit,
            "applied": self.applied,
            "notes": list(self.notes),
            "detail": self.detail,
        }


def advice(mode: Mode) -> str:
    """动作对应的处置建议。"""
    return MODE_ADVICE[mode.value]


def decide(pressure: float | None, limit: float) -> tuple[Mode, int]:
    """按 PSI 争用决定权重；读不到争用即维持基线。"""
    if pressure is not None and pressure >= limit:
        return Mode.PROTECT, PROTECT_WEIGHT
    return Mode.NORMAL, BASE_WEIGHT


def inspect(cfg: Coexist) -> Plan:
    """只读评估，供巡检与门禁使用。"""
    return _assess(cfg, False)


def apply(cfg: Coexist) -> Plan:
    """评估并落地权重，供周期单元使用。"""
    return _assess(cfg, True)


def _assess(cfg: Coexist, push: bool) -> Plan:
    found = matrix.collect(cfg.roots)
    state = _query(cfg, "LoadState")
    if state is None:
        return _plan(cfg, found, Mode.FAILED, detail="LoadState 不可读")
    if state != "loaded":
        return _plan(cfg, found, Mode.ABSENT, detail=f"LoadState={state}")
    seen = _sample(cfg)
    mode, weight = decide(seen.value, _limit(cfg, seen.source))
    current = _number(_query(cfg, "CPUWeight"))
    applied = current == weight
    detail = ""
    if push and not applied:
        current = _set(cfg, weight)
        applied = current == weight
        if not applied:
            detail = "set-property 未生效"
    return _plan(cfg, found, mode, weight=weight, current=current,
                 seen=seen, present=True, applied=applied, detail=detail)


def _plan(cfg: Coexist, found: matrix.Matrix, mode: Mode, *,
          weight: int = 0, current: int | None = None,
          seen: Reading = Reading(None), present: bool = False,
          applied: bool = False, detail: str = "") -> Plan:
    return Plan(mode, cfg.unit, weight, current, seen.value, found.grade,
                present, applied, _notes(found, seen, present), detail,
                seen.source.value, seen.cores)


def _notes(found: matrix.Matrix, seen: Reading,
           present: bool) -> tuple[str, ...]:
    items: list[str] = []
    if found.flag(matrix.GOVERNOR) is matrix.Flag.NO:
        items.append("调频非 performance：实时延迟受省电策略影响")
    if found.flag(matrix.ISOLATED) is matrix.Flag.NO:
        items.append("未隔离 CPU：实时与桌面共享全部核心")
    if found.flag(matrix.THREADIRQS) is matrix.Flag.NO:
        items.append("中断未线程化：延迟抖动可能来自硬中断")
    if present and seen.source is Source.NONE:
        items.append("无 CPU 争用读数：按基线权重运行")
    if present and seen.source is Source.LOAD:
        items.append(
            f"无 PSI 计数：以 {seen.cores} 核 1 分钟负载均值判定"
        )
    return tuple(items)


def _sample(cfg: Coexist) -> Reading:
    """先取 PSI；没有 PSI 时退回按核数归一的负载均值。"""
    psi = _read(cfg.pressure)
    if psi is not None:
        return Reading(psi, Source.PSI)
    cores = _cores(Path(cfg.roots.proc) / CPUINFO)
    load = _load1(cfg.loadavg)
    if not cores or load is None:
        return Reading(None, Source.NONE, cores)
    return Reading(round(load * 100 / cores, 1), Source.LOAD, cores)


def _limit(cfg: Coexist, source: Source) -> float:
    """负载均值含不可中断睡眠，只有整机平均满载才抬权重。"""
    if source is Source.LOAD:
        return cfg.load_limit
    return cfg.limit


def _cores(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="ascii")
    except OSError:
        return None
    count = sum(
        1 for line in text.splitlines() if line.startswith("processor")
    )
    return count or None


def _load1(path: str) -> float | None:
    try:
        text = Path(path).read_text(encoding="ascii")
    except OSError:
        return None
    head = text.split(maxsplit=1)
    if not head:
        return None
    try:
        return float(head[0])
    except ValueError:
        return None


def _read(path: str) -> float | None:
    try:
        text = Path(path).read_text(encoding="ascii")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("some "):
            return _field(line, "avg10")
    return None


def _field(line: str, key: str) -> float | None:
    for token in line.split()[1:]:
        name, _, value = token.partition("=")
        if name != key:
            continue
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _query(cfg: Coexist, key: str) -> str | None:
    done = _run(cfg, ("show", "-p", key, "--value", cfg.unit))
    if done is None or done.returncode != 0:
        return None
    return done.stdout.strip()


def _set(cfg: Coexist, weight: int) -> int | None:
    done = _run(cfg, ("set-property", "--runtime", cfg.unit,
                      f"CPUWeight={weight}"))
    if done is None or done.returncode != 0:
        return None
    return _number(_query(cfg, "CPUWeight"))


def _run(cfg: Coexist, args: tuple[str, ...]):
    try:
        return subprocess.run(
            [cfg.systemctl, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _number(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return int(text, 10)
    except ValueError:
        return None
