# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 实时带宽所在的 cgroup 布局识别
#
# 同一份策略要在两种内核布局上落地：v1 把 cpu 控制器挂在独立挂载点
# （openEuler 上是 /sys/fs/cgroup/cpu,cpuacct），实时预算文件叫
# cpu.rt_runtime_us；v2 只有一棵统一树，文件叫 cpu.rt.max，取值形如
# 「预算 周期」。写错布局会得到 ENOENT 或 EINVAL，看起来都像「没有权限」，
# 排查时最耗时。因此先反查真实挂载根，再决定文件名与写入格式。
#
# 两种「不可记账」必须区分：v2 的 cgroup.controllers 里没有 cpu，说明
# 这台机器根本没把 cpu 控制器交给 cgroup；有 cpu 却没有 cpu.rt.max，
# 说明内核没编 RT 组调度。前者写任何文件都无意义，后者写进去也不会被
# 记账，验收侧要分别给出结论而不是笼统报失败。
#
# 设计依据：docs-linux/100-operations/02-configuration.md §2.2。

"""AirymaxOS RT bandwidth cgroup layout (X-01)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path

__all__ = [
    "CONTROLLER",
    "SUBTREE",
    "V1_FILE",
    "V2_FILE",
    "Kind",
    "Layout",
    "detect",
    "read",
    "target",
    "write",
]

V1_FILE = "cpu.rt_runtime_us"

V2_FILE = "cpu.rt.max"

CONTROLLER = "cgroup.controllers"

SUBTREE = "cgroup.subtree_control"

CPU = "cpu"


@unique
class Kind(Enum):
    """cgroup 记账布局。"""

    V1 = "v1"
    V2 = "v2"


@dataclass(frozen=True, slots=True)
class Layout:
    """一次识别结果。

    ``supported`` 只对 v2 有意义：统一树存在但缺少 ``cpu.rt.max`` 时，
    布局可用而预算不可记账，判定为 unaccounted。v1 只要找到挂载根即可写。
    ``file`` 为空表示统一树没有把 cpu 控制器分发给子组，此时连预算文件
    都不存在，与「有文件但未记账」是两种不同的故障。
    """

    kind: Kind
    root: str
    file: str
    supported: bool

    @property
    def routed(self) -> bool:
        """cpu 控制器是否已经落到可写的层级上。"""
        return self.kind is Kind.V1 or bool(self.file)


def detect(root: str) -> Layout | None:
    """从挂载根识别布局；整棵记账树都不存在时返回 None。"""
    base = Path(root)
    if (base / CONTROLLER).is_file():
        try:
            text = (base / CONTROLLER).read_text(encoding="ascii")
        except OSError:
            return None
        if CPU not in text.split():
            return Layout(Kind.V2, str(base), "", False)
        has_rt = (base / V2_FILE).is_file()
        return Layout(Kind.V2, str(base), V2_FILE, has_rt)
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return None
    for entry in entries:
        if (entry / V1_FILE).is_file():
            return Layout(Kind.V1, str(entry), V1_FILE, True)
    return None


def target(layout: Layout, name: str) -> Path:
    """某个 slice 在布局中的目录。"""
    return Path(layout.root) / name


def read(layout: Layout, name: str) -> int | None:
    """读取已落地的预算；v2 的 ``max`` 表示不限，按不可比处理。"""
    path = target(layout, name) / layout.file
    try:
        text = path.read_text(encoding="ascii").strip()
    except OSError:
        return None
    if not text:
        return None
    head = text.split()[0]
    if head == "max":
        return None
    try:
        return int(head, 10)
    except ValueError:
        return None


def write(layout: Layout, name: str, budget: int, period: int) -> bool:
    """写入一个 slice 的预算，并以回读结果作为成功判据。

    回读不是冗余校验：cgroupfs 对层级预算有约束（子组之和不得超过父组），
    越界写入会返回 EINVAL，而某些内核版本会把越界值静默夹到父组上限。
    只信写入返回值会把「没生效」记成「已生效」，验收门禁因此形同虚设。
    """
    if not layout.supported or budget <= 0:
        return False
    if layout.kind is Kind.V2 and not _expose(layout):
        return False
    path = target(layout, name)
    try:
        path.mkdir(parents=True, exist_ok=True)
        payload = f"{budget}"
        if layout.kind is Kind.V2:
            payload = f"{budget} {period}"
        (path / layout.file).write_text(payload + "\n", encoding="ascii")
    except OSError:
        return False
    return read(layout, name) == budget


def _expose(layout: Layout) -> bool:
    """确保统一树的根把 cpu 控制器分发给子组，否则子组没有预算文件。

    根 cgroup 不受「内部进程」约束，向根写 subtree_control 是允许的；
    已经分发过时重复追加 ``+cpu`` 也是幂等的。
    """
    path = Path(layout.root) / SUBTREE
    try:
        text = path.read_text(encoding="ascii")
    except OSError:
        return False
    if CPU in {token.lstrip("+-") for token in text.split()}:
        return True
    try:
        path.write_text(text.rstrip() + " +cpu\n", encoding="ascii")
    except OSError:
        return False
    return True
