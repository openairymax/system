# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# sysfs 文本节点的读写
#
# 电源与温度接口分散在三棵子树里，形状却只有两种：单值文本与整数计数。
# 读取侧必须把「文件不存在」「内容为空」「内容不是整数」统一折成
# None，否则上层会把「本机不提供该控制面」误判成「值为 0」，进而写出
# 一个把性能锁死或把保护关掉的档位。写入侧只回答「内核收没接受」，
# 是否生效一律由调用方回读判定。

"""AirymaxOS sysfs helpers."""

from __future__ import annotations

from pathlib import Path

__all__ = ["order", "read_int", "read_text", "write_text"]


def read_text(path: Path) -> str | None:
    """读取一个文本节点；缺失、为空或不可读返回 None。"""
    try:
        text = path.read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return None
    return text or None


def read_int(path: Path) -> int | None:
    """读取一个整数节点；缺失或非整数返回 None。"""
    text = read_text(path)
    if text is None:
        return None
    try:
        return int(text, 10)
    except ValueError:
        return None


def write_text(path: Path, value: str) -> bool:
    """写入一个文本节点，返回值只表示内核是否接受。"""
    try:
        path.write_text(value + "\n", encoding="ascii")
    except OSError:
        return False
    return True


def order(path: Path) -> tuple[int, str]:
    """按名字尾部序号排序，thermal_zone2 排在 thermal_zone10 之前。

    序号一律取名字末尾的连续数字，而不是最后一个下划线之后的部分：
    thermal_zone2、cooling_device2、state2 的序号都在「_」之后还带着词干，
    按词干取会把它们全部折成 0，最终退化成字典序，出现 zone10 在 zone2
    之前、证据顺序与硬件编号对不上的现场报告。
    """
    stem = path.name.rstrip("0123456789")
    tail = path.name[len(stem):]
    return (int(tail) if tail else 0, path.name)
