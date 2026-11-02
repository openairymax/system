# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 模型运行判定
#
# 本层把四件事按固定顺序串起来：读清单、核资产、查引擎、选后端。顺序
# 不可换——先选后端再核资产，就会出现「探测到 GPU 但权重损坏」仍报就绪
# 的窗口；反之则一段损坏的权重只会得到一条准确的归因，而不是一串推测。
#
# 判定结果无论好坏都落盘：服务起不来时，外部工具唯一的线索就是这份
# 状态文件，失败路径不写状态等于把根因留在日志里。
#
# 已经落盘的结论可以复用，但只在清单摘要未变时复用：换了一版权重却仍
# 报上一版的结果，等于给用户一份比没有记录更坏的答复。因此复用与否
# 由摘要判定，不设时效，也不信任文件的修改时间。

"""AirymaxOS model runtime resolution (K-20-07).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
（完全断网可自检、清单与实物不符即硬失败）。

"""

from __future__ import annotations

from pathlib import Path

from . import accel, manifest, model, paths, runtime, status, verify
from .failure import Failure

__all__ = ["assess", "cached", "resolve"]


def assess(root: str | Path | None = None,
           present: frozenset[model.Backend] | None = None
           ) -> tuple[model.Manifest | None, model.Status]:
    """得出一次运行判定并落盘，同时交出判定所依据的清单。

    启动层要用清单派生引擎参数，若它自行再读一次清单，两次读取之间
    清单可能被替换，参数就会与状态里记录的摘要不是同一份。

    Args:
        root: 运行根；``None`` 时取契约根。
        present: 可用的加速后端；``None`` 时现场探测。

    Returns:
        ``(清单, 判定)``；判定为 ``FAILED`` 时清单为 ``None``。
    """
    home = paths.base(root)
    try:
        entry = manifest.load(paths.manifest(home))
        verify.check(home, entry)
        runtime.check(entry)
    except Failure as exc:
        return None, _save(home, model.Status(state=model.State.FAILED,
                                              backend=model.Backend.CPU,
                                              reason=exc.reason))
    digest = verify.digest(paths.manifest(home))
    try:
        backend, reason = accel.pick(entry.runtime.backends,
                                     _seen(present))
    except Failure as exc:
        return None, _save(home, model.Status(state=model.State.FAILED,
                                              backend=model.Backend.CPU,
                                              reason=exc.reason,
                                              digest=digest))
    state = model.State.READY if reason is None else model.State.DEGRADED
    return entry, _save(home, model.Status(state=state, backend=backend,
                                           reason=reason, digest=digest))


def resolve(root: str | Path | None = None,
            present: frozenset[model.Backend] | None = None) -> model.Status:
    """得出一次运行判定并落盘。

    Args:
        root: 运行根；``None`` 时取契约根。
        present: 可用的加速后端；``None`` 时现场探测。

    Returns:
        本次判定；失败时不抛异常，改为 ``FAILED`` 加归因。
    """
    return assess(root, present)[1]


def cached(root: str | Path | None = None,
           present: frozenset[model.Backend] | None = None) -> model.Status:
    """给出可对外展示的判定，尽量不做整份资产的摘要计算。

    查询状态的调用方不承担校验职责：权重有数 GB，每次查询都通读一遍
    只为了让用户看一眼结论。因此清单未变时直接复用上一次判定，变了
    就按当前清单重算。

    Args:
        root: 运行根；``None`` 时取契约根。
        present: 可用的加速后端；非 ``None`` 表示调用方要求按这份后端
            集合重新判定，此时不复用缓存。

    Returns:
        与 :func:`resolve` 同形的判定。
    """
    home = paths.base(root)
    kept = status.load(home)
    if _fresh(home, kept, present):
        return kept
    return resolve(home, present)


def _fresh(home: Path, kept: model.Status | None,
           present: frozenset[model.Backend] | None) -> bool:
    return (present is None and kept is not None
            and kept.digest != "" and kept.digest == _digest(home))


def _digest(home: Path) -> str | None:
    try:
        return verify.digest(paths.manifest(home))
    except OSError:
        return None


def _seen(present: frozenset[model.Backend] | None) -> frozenset:
    return accel.detect() if present is None else present


def _save(home: Path, entry: model.Status) -> model.Status:
    status.store(home, entry)
    return entry
