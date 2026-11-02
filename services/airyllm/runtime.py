# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 推理引擎检查
#
# 清单只声明引擎在哪、是什么版本，不保证磁盘上还是那一份。权重与引擎是
# 两份独立交付物：升级系统包可能换掉引擎，而清单仍是旧的。判定必须读
# 一次磁盘，否则状态会报就绪、服务一启动就崩在 execve 上，或更糟——用
# 一个参数语义已变的引擎加载旧清单。
#
# 版本取自引擎目录下的标记文件，而不是执行引擎去问：版本探测要跑程序，
# 可能阻塞或触发驱动加载，属于后端探测层；标记文件由引擎包在构建时写入，
# 与二进制同生共死。
#
# 只做只读检查，不执行引擎。

"""AirymaxOS inference engine inspection (K-20-09).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
（运行期不发起网络请求、清单与实物不符即硬失败）。

"""

from __future__ import annotations

import stat
from pathlib import Path

from . import model
from .failure import Failure

__all__ = ["VERSION_FILE", "check"]

EXEC_BITS = 0o111

VERSION_FILE = "ENGINE_VERSION"


def check(entry: model.Manifest) -> None:
    """确认清单指定的引擎可执行且版本相符。

    Args:
        entry: 已解析的清单。

    Raises:
        Failure: 引擎路径非绝对、不存在、非普通文件、无可执行位，或版本
            与清单不符。
    """
    path = Path(entry.runtime.path)
    if not path.is_absolute():
        raise Failure(f"引擎路径必须是绝对路径: {entry.runtime.path!r}",
                      model.Reason.NO_RUNTIME, entry.runtime.path)
    try:
        info = path.stat()
    except OSError as exc:
        raise Failure(f"引擎不可访问: {entry.runtime.path}",
                      model.Reason.NO_RUNTIME, entry.runtime.path) from exc
    if not stat.S_ISREG(info.st_mode):
        raise Failure(f"引擎不是普通文件: {entry.runtime.path}",
                      model.Reason.NO_RUNTIME, entry.runtime.path)
    if not info.st_mode & EXEC_BITS:
        raise Failure(f"引擎没有可执行位: {entry.runtime.path}",
                      model.Reason.NO_RUNTIME, entry.runtime.path)
    _version(entry, path)


def _version(entry: model.Manifest, path: Path) -> None:
    marker = path.parent / VERSION_FILE
    try:
        got = marker.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise Failure(f"引擎缺少版本标记: {marker}",
                      model.Reason.NO_RUNTIME, str(marker)) from exc
    if got != entry.runtime.version:
        raise Failure(
            f"引擎版本 {got!r} 与清单 {entry.runtime.version!r} 不符",
            model.Reason.NO_RUNTIME, str(marker))
