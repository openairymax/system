# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 推理服务启动
#
# 启动只做三件事：判定、拼参数、把进程换成引擎。不在本层常驻——常驻
# 就要自己转发信号、回收子进程、复述日志，而 systemd 已经做了这些，
# 多一层只会让「引擎实际以什么参数运行」与「单元文件写了什么」分离。
#
# 判定失败时以区别于普通退出的码结束，让 systemd 把它记成配置错误而
# 不是崩溃；两者在运维上的处置完全不同。
#
# 判定通过后还要过一道准入：资产齐全不代表装得下。285H 上三个实例把 RSS
# 叠到 29.5 G / 30 G，换页写回把唤醒延迟推到毫秒级，而单元的重启策略会把
# 「装不下」变成「反复起、反复拖慢」。因此拉起前按现场可用内存判定，装
# 不下就以独立的码结束并给出缺口，不降级、不排队。内存接口读不到时放行
# 并留一行依据：拒绝启动的代价是模型永远起不来，比一次可观测的越界更糟。
#
# 降级或失败时连探测依据一起写进日志：只报一句「没有加速后端」，用户
# 无从判断是驱动缺失、设备节点不可访问，还是 ICD 清单指向了不存在的库。
# 依据只在本层现场探测时才有，调用方给定后端集合时不再重复探测。

"""AirymaxOS inference service launcher (K-20-12).

设计依据：docs-linux/100-operations/07-systemd-integration.md §3
（ExecStart 直接拉起服务进程，重启策略由单元承担）与
docs-linux/170-performance/07-rt-matrix.md §5.5（启动前准入，只拒绝
不驱逐）。

"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

from airymax_rt import admit

from . import accel, engine, model, paths, report, serve

__all__ = ["EXIT_REFUSED", "EXIT_UNAVAILABLE", "plan", "run"]

EXIT_UNAVAILABLE = 78

EXIT_REFUSED = 75


def plan(root: str | Path | None = None,
         present: frozenset[model.Backend] | None = None
         ) -> tuple[model.Status, list[str], int]:
    """判定并派生启动参数与常驻量。

    Args:
        root: 运行根；``None`` 时取契约根。
        present: 可用的加速后端；``None`` 时现场探测。

    Returns:
        ``(判定, 参数列表, 常驻量)``；判定为 ``FAILED`` 时后两项为空与零。
    """
    entry, result = engine.assess(root, present)
    if entry is None:
        return result, [], 0
    return (result, serve.argv(entry, result.backend, paths.base(root)),
            serve.need(entry))


def run(root: str | Path | None = None,
        present: frozenset[model.Backend] | None = None,
        check: Callable[[int], admit.Verdict] | None = None) -> int:
    """拉起推理引擎；成功时本函数不返回。

    Args:
        root: 运行根；``None`` 时取契约根。
        present: 可用的加速后端；``None`` 时现场探测。
        check: 准入判定实现；``None`` 时按现场内存判定。

    Returns:
        仅在无法启动时返回 :data:`EXIT_UNAVAILABLE` 或
        :data:`EXIT_REFUSED`；降级启动照常进行，但先把降级原因与探测
        依据写进日志，避免用户以为在用加速后端。
    """
    rows = accel.probe() if present is None else ()
    result, argv, need = plan(root, present if present is not None
                              else accel.present(rows))
    if not argv:
        print(f"airy-llm: {report.token(result)}", file=sys.stderr)
        _decline(rows)
        return EXIT_UNAVAILABLE
    if _refuse(need, check):
        return EXIT_REFUSED
    if result.state is model.State.DEGRADED:
        print(f"airy-llm: {report.token(result)}", file=sys.stderr)
        _decline(rows)
    try:
        os.execv(argv[0], argv)
    except OSError as exc:
        print(f"airy-llm: exec: {exc}", file=sys.stderr)
    return EXIT_UNAVAILABLE


def _refuse(need: int, check: Callable[[int], admit.Verdict] | None
            ) -> bool:
    """按现场内存判定能否拉起；判定不可得时放行并留依据。"""
    found = (admit.check if check is None else check)(need)
    if found.state == admit.REFUSE:
        print(f"airy-llm: admit: refuse need={found.need}"
              f" free={found.free} short={found.short}", file=sys.stderr)
        return True
    if found.state != admit.FIT:
        print(f"airy-llm: admit: unknown need={found.need}"
              f" free={found.free}，按可启动处理", file=sys.stderr)
    return False


def _decline(rows: tuple[accel.Observation, ...]) -> None:
    """逐条打印未命中的后端及其探测依据；调用方给定集合时无依据可打。"""
    for row in rows:
        if not row.present:
            print(f"airy-llm: probe: {row.backend.value} "
                  f"{' '.join(row.evidence)}", file=sys.stderr)
