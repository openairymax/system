# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 磁盘动作执行器
#
# 执行面只做三件事：按 argv 起进程、把退出码与输出原样带回、失败时
# 反向回滚已完成的步骤。不经 shell，参数不做二次解释；编排全部来自
# 计划，执行器没有机型、盘符或文件系统分支。回滚不成功时置 unclean，
# 让上层把「系统停在中间态」如实报出来，而不是只报一个失败码。

"""AirymaxOS disk step execution and rollback (M-31).

设计依据：docs-linux/190-distribution/03-installer-design.md §3 与
0.0.4 方案 §6.3 M-31。失败即回滚：已完成的步骤按相反顺序执行其
``undo``，缺 ``undo`` 的步骤视为不可逆，回滚后标记 ``unclean``。

"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from .ops import Plan, Step

__all__ = [
    "RC_ABORT",
    "RC_MISSING",
    "RC_TIMEOUT",
    "TIMEOUT",
    "Outcome",
    "Result",
    "Runner",
    "apply",
    "run",
]

TIMEOUT = 120

RC_TIMEOUT = 124

RC_ABORT = 126

RC_MISSING = 127


@dataclass(frozen=True, slots=True)
class Result:
    """单步执行结果；``rc`` 沿用 shell 约定，124 超时、126 不可执行。"""

    step: Step
    rc: int
    out: str = ""
    err: str = ""

    @property
    def ok(self) -> bool:
        return self.rc == 0


@dataclass(frozen=True, slots=True)
class Outcome:
    """一次计划的执行结果；``unclean`` 表示回滚未能撤净。"""

    plan: Plan
    done: tuple[Step, ...] = ()
    failed: Result | None = None
    undone: tuple[Step, ...] = ()
    unclean: bool = False

    @property
    def ok(self) -> bool:
        return self.failed is None


Runner = Callable[[Step, float], Result]


def run(step: Step, timeout: float = TIMEOUT) -> Result:
    """执行单个步骤并返回结果；进程起不来或超时都折算成退出码。"""
    try:
        done = subprocess.run(
            list(step.argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Result(step=step, rc=RC_TIMEOUT, err="timeout")
    except FileNotFoundError as exc:
        return Result(step=step, rc=RC_MISSING, err=str(exc))
    except OSError as exc:
        return Result(step=step, rc=RC_ABORT, err=str(exc))
    return Result(
        step=step,
        rc=done.returncode,
        out=done.stdout or "",
        err=done.stderr or "",
    )


def apply(
    plan: Plan, runner: Runner = run, timeout: float = TIMEOUT
) -> Outcome:
    """按顺序执行计划；任一步失败即回滚已完成的步骤。"""
    done: list[Step] = []
    for step in plan.steps:
        result = runner(step, timeout)
        if not result.ok:
            undone, unclean = _undo(done, runner, timeout)
            return Outcome(
                plan=plan,
                done=tuple(done),
                failed=result,
                undone=undone,
                unclean=unclean,
            )
        done.append(step)
    return Outcome(plan=plan, done=tuple(done))


def _undo(
    done: list[Step], runner: Runner, timeout: float
) -> tuple[tuple[Step, ...], bool]:
    """反向回滚；缺 undo 或回滚失败都记为 unclean。"""
    undone: list[Step] = []
    unclean = False
    for step in reversed(done):
        if not step.undo:
            unclean = True
            continue
        back = Step(action=step.action, target=step.target, argv=step.undo)
        if runner(back, timeout).ok:
            undone.append(step)
        else:
            unclean = True
    return tuple(undone), unclean
