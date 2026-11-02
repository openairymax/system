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
# 降级或失败时连探测依据一起写进日志：只报一句「没有加速后端」，用户
# 无从判断是驱动缺失、设备节点不可访问，还是 ICD 清单指向了不存在的库。
# 依据只在本层现场探测时才有，调用方给定后端集合时不再重复探测。

"""AirymaxOS inference service launcher (K-20-12).

设计依据：docs-linux/100-operations/07-systemd-integration.md §3
（ExecStart 直接拉起服务进程，重启策略由单元承担）。

"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import accel, engine, model, paths, report, serve

__all__ = ["EXIT_UNAVAILABLE", "plan", "run"]

EXIT_UNAVAILABLE = 78


def plan(root: str | Path | None = None,
         present: frozenset[model.Backend] | None = None
         ) -> tuple[model.Status, list[str]]:
    """判定并派生启动参数。

    Args:
        root: 运行根；``None`` 时取契约根。
        present: 可用的加速后端；``None`` 时现场探测。

    Returns:
        ``(判定, 参数列表)``；判定为 ``FAILED`` 时参数为空。
    """
    entry, result = engine.assess(root, present)
    if entry is None:
        return result, []
    return result, serve.argv(entry, result.backend, paths.base(root))


def run(root: str | Path | None = None,
        present: frozenset[model.Backend] | None = None) -> int:
    """拉起推理引擎；成功时本函数不返回。

    Args:
        root: 运行根；``None`` 时取契约根。
        present: 可用的加速后端；``None`` 时现场探测。

    Returns:
        仅在无法启动时返回 :data:`EXIT_UNAVAILABLE`；降级启动照常进行，
        但先把降级原因与探测依据写进日志，避免用户以为在用加速后端。
    """
    rows = accel.probe() if present is None else ()
    result, argv = plan(root, present if present is not None
                        else accel.present(rows))
    if not argv:
        print(f"airy-llm: {report.token(result)}", file=sys.stderr)
        _decline(rows)
        return EXIT_UNAVAILABLE
    if result.state is model.State.DEGRADED:
        print(f"airy-llm: {report.token(result)}", file=sys.stderr)
        _decline(rows)
    try:
        os.execv(argv[0], argv)
    except OSError as exc:
        print(f"airy-llm: exec: {exc}", file=sys.stderr)
    return EXIT_UNAVAILABLE


def _decline(rows: tuple[accel.Observation, ...]) -> None:
    """逐条打印未命中的后端及其探测依据；调用方给定集合时无依据可打。"""
    for row in rows:
        if not row.present:
            print(f"airy-llm: probe: {row.backend.value} "
                  f"{' '.join(row.evidence)}", file=sys.stderr)
