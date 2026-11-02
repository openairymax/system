# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 推理加速后端选择
#
# 后端有两重来源：引擎编译期支持哪些（清单声明），机器上实际有哪些
# （设备节点与 ICD 清单）。两者求交才可用，交集为空就走 CPU，并把
# no-accel 写进状态——静默降级会让用户以为在用 GPU，实际慢一个量级。
#
# 探测只读文件系统，不加载驱动、不调用厂商库：判定要在服务启动的
# 早期完成，任何可能阻塞或崩溃的调用都不能出现在这里。

"""AirymaxOS accelerator backend selection (K-20-06).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
（后端探测不到即降级并留因，不得静默）。

"""

from __future__ import annotations

from pathlib import Path

from . import model
from .failure import Failure

__all__ = ["ORDER", "detect", "pick"]

ORDER = (
    model.Backend.CUDA,
    model.Backend.ROCM,
    model.Backend.VULKAN,
    model.Backend.CPU,
)


def detect(root: str | Path = "/") -> frozenset[model.Backend]:
    """探测机器上可用的加速后端。

    Args:
        root: 文件系统根；测试可指向夹具，生产传 ``/``。

    Returns:
        可用的加速后端集合；不含 CPU，CPU 恒可用。
    """
    base = Path(root)
    found = set()
    if (base / "dev/nvidia0").exists():
        found.add(model.Backend.CUDA)
    if (base / "dev/kfd").exists():
        found.add(model.Backend.ROCM)
    if _vulkan(base):
        found.add(model.Backend.VULKAN)
    return frozenset(found)


def pick(supported: tuple[model.Backend, ...],
         present: frozenset[model.Backend]) -> tuple[model.Backend,
                                                    model.Reason | None]:
    """在支持集与可用集的交集里按固定优先级选一个后端。

    Args:
        supported: 引擎编译期支持的后端。
        present: 机器上探测到的加速后端。

    Returns:
        ``(后端, None)``；退到 CPU 时为 ``(CPU, NO_ACCEL)``。

    Raises:
        Failure: 引擎连 CPU 后端都没有，属清单与实物不符。
    """
    for backend in ORDER:
        if backend in supported and backend in present:
            return backend, None
    if model.Backend.CPU not in supported:
        raise Failure("引擎未编译 CPU 后端", model.Reason.NO_RUNTIME)
    return model.Backend.CPU, model.Reason.NO_ACCEL


def _vulkan(base: Path) -> bool:
    icd = base / "usr/share/vulkan/icd.d"
    if not (base / "dev/dri").is_dir():
        return False
    return icd.is_dir() and any(icd.glob("*.json"))
