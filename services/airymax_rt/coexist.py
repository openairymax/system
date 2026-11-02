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
# 触发信号用 PSI 的 CPU some avg10——它度量「有任务可运行却在等 CPU」的
# 时间占比，正是桌面失响应的直接指标；/proc/loadavg 把不可中断睡眠也算
# 进去，会在等 IO 时误判。读不到 PSI 时不提升权重：没有证据不做调整。
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
    "DEFAULT_PRESSURE",
    "DEFAULT_SYSTEMCTL",
    "LIMIT",
    "PROTECT_WEIGHT",
    "Coexist",
    "Mode",
    "Plan",
    "advice",
    "apply",
    "decide",
    "inspect",
]

DEFAULT_SYSTEMCTL = "/usr/bin/systemctl"

DEFAULT_DESKTOP = "airymax-desktop.slice"

DEFAULT_PRESSURE = "/proc/pressure/cpu"

BASE_WEIGHT = 50

PROTECT_WEIGHT = 200

LIMIT = 20.0

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


@dataclass(frozen=True, slots=True)
class Coexist:
    """一次评估的输入；单元名与路径可替换，便于测试与救援环境。"""

    unit: str = DEFAULT_DESKTOP
    systemctl: str = DEFAULT_SYSTEMCTL
    pressure: str = DEFAULT_PRESSURE
    limit: float = LIMIT
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
    pressure = _read(cfg.pressure)
    mode, weight = decide(pressure, cfg.limit)
    current = _number(_query(cfg, "CPUWeight"))
    applied = current == weight
    detail = ""
    if push and not applied:
        current = _set(cfg, weight)
        applied = current == weight
        if not applied:
            detail = "set-property 未生效"
    return _plan(cfg, found, mode, weight=weight, current=current,
                 pressure=pressure, present=True, applied=applied,
                 detail=detail)


def _plan(cfg: Coexist, found: matrix.Matrix, mode: Mode, *,
          weight: int = 0, current: int | None = None,
          pressure: float | None = None, present: bool = False,
          applied: bool = False, detail: str = "") -> Plan:
    return Plan(mode, cfg.unit, weight, current, pressure, found.grade,
                present, applied, _notes(found, pressure, present), detail)


def _notes(found: matrix.Matrix, pressure: float | None,
           present: bool) -> tuple[str, ...]:
    items: list[str] = []
    if found.flag(matrix.GOVERNOR) is matrix.Flag.NO:
        items.append("调频非 performance：实时延迟受省电策略影响")
    if found.flag(matrix.ISOLATED) is matrix.Flag.NO:
        items.append("未隔离 CPU：实时与桌面共享全部核心")
    if found.flag(matrix.THREADIRQS) is matrix.Flag.NO:
        items.append("中断未线程化：延迟抖动可能来自硬中断")
    if present and pressure is None:
        items.append("无 PSI 计数：按基线权重运行")
    return tuple(items)


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
