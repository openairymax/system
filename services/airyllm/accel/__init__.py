# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 加速后端选择
#
# 探测回答「有什么」，选择回答「用哪个」。两者分开是因为调用方需要的粒度
# 不同：启动层要完整观察以便解释降级，状态缓存只需要后端集合。
#
# 选择顺序固定为 CUDA、ROCm、Vulkan、CPU。顺序即优先级，不做运行时打分：
# 换一次驱动就让服务换一次后端，运维无法建立稳定预期；固定顺序至少让
# 「为什么是 ROCm 而不是 Vulkan」有确定答案。

"""AirymaxOS accelerator backend selection (K-20-06).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
（探测不到加速后端按 CPU 路径运行并留 degraded:no-accel；引擎未编译 CPU
后端属硬失败）。

"""

from __future__ import annotations

from pathlib import Path

from .. import model
from ..failure import Failure
from .probe import ORDER, Observation, present, probe

__all__ = ["ORDER", "Observation", "detect", "pick", "present", "probe"]


def detect(root: str | Path = "/") -> frozenset[model.Backend]:
    """给出当前可用的加速后端集合。

    Args:
        root: 运行根，通常是 ``/``。

    Returns:
        可用加速后端；CPU 不计入，由 :func:`pick` 兜底。
    """
    return present(probe(root))


def pick(supported: tuple[model.Backend, ...],
         seen: frozenset[model.Backend]
         ) -> tuple[model.Backend, model.Reason | None]:
    """从支持集与可用集的交集中按固定优先级取第一个。

    Args:
        supported: 引擎编译期支持的后端。
        seen: 当前可用的加速后端。

    Returns:
        ``(后端, 原因)``；选中加速后端时原因为 ``None``，退 CPU 时为
        :data:`model.Reason.NO_ACCEL`。

    Raises:
        Failure: 引擎未编译 CPU 后端且没有可用的加速后端。
    """
    for backend in ORDER:
        if backend in supported and backend in seen:
            return backend, None
    if model.Backend.CPU not in supported:
        raise Failure("引擎未编译 CPU 后端", model.Reason.NO_RUNTIME)
    return model.Backend.CPU, model.Reason.NO_ACCEL
