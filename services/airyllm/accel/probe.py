# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 加速后端探测
#
# 每个后端给出一条观察：是否可用，以及依据了哪些事实。探测只做静态判定，
# 不加载驱动、不创建上下文——把引擎加载失败的风险提前到启动前暴露，同时
# 保证探测在无显卡机器上也是毫秒级返回。
#
# 依据必须逐条保留：降级时运维要能回答「为什么没用 GPU」，只报一个布尔值
# 等于把这个问题留给下一次排障。
#
# 判定口径按引擎的实际需要收敛：CUDA 同时要控制节点与设备节点，ROCm 要
# KFD 与至少一个渲染节点，Vulkan 要 DRM 目录与一份能解析出库的 ICD 清单。
# 多卡机器上一张卡不可用不应否决整个后端，故 ROCm 取「任一」。

"""AirymaxOS accelerator probing (K-22-01).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
（探测不到加速后端按 CPU 路径运行并留 degraded:no-accel）。

"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import model
from . import device, icd

__all__ = ["BUILTIN", "ORDER", "Observation", "present", "probe"]

BUILTIN = "builtin"

ORDER = (
    model.Backend.CUDA,
    model.Backend.ROCM,
    model.Backend.VULKAN,
    model.Backend.CPU,
)


@dataclass(frozen=True, slots=True)
class Observation:
    """单个后端的探测结论。

    Attributes:
        backend: 后端。
        present: 当前进程身份是否可用。
        evidence: 判定依据，每项以 ``ok:``、``missing:``、``denied:``、
            ``empty:``、``stale:``、``unverified:`` 或 :data:`BUILTIN`
            开头。
    """

    backend: model.Backend
    present: bool
    evidence: tuple[str, ...]


def probe(root: str | Path = "/") -> tuple[Observation, ...]:
    """探测全部后端。

    Args:
        root: 运行根，通常是设备节点与 ICD 清单所在的 ``/``。

    Returns:
        四个后端的观察，顺序固定为 :data:`ORDER`；CPU 恒为可用。
    """
    base = Path(root)
    return (_cuda(base), _rocm(base), _vulkan(base), _cpu())


def present(rows: tuple[Observation, ...]) -> frozenset[model.Backend]:
    """从观察里挑出可用的加速后端。

    Args:
        rows: :func:`probe` 的结果。

    Returns:
        可用加速后端集合；CPU 不计入——它恒可用，由选择层兜底。
    """
    return frozenset(row.backend for row in rows
                     if row.present and row.backend is not model.Backend.CPU)


def _cuda(base: Path) -> Observation:
    checks = (device.usable(base, "dev/nvidiactl"),
              device.usable(base, "dev/nvidia0"))
    return Observation(model.Backend.CUDA, all(item[0] for item in checks),
                       tuple(item[1] for item in checks))


def _rocm(base: Path) -> Observation:
    ok, why = device.usable(base, "dev/kfd")
    nodes = device.renders(base)
    good = next((item[1] for item in nodes if item[0]), "")
    evidence = (why,) if not good else (why, good)
    return Observation(model.Backend.ROCM, ok and bool(good), evidence)


def _vulkan(base: Path) -> Observation:
    if not (base / "dev/dri").is_dir():
        return Observation(model.Backend.VULKAN, False,
                           (device.MISSING + "dev/dri",))
    ok, why = icd.first(base)
    return Observation(model.Backend.VULKAN, ok,
                       (device.OK + "dev/dri", why))


def _cpu() -> Observation:
    return Observation(model.Backend.CPU, True, (BUILTIN,))
