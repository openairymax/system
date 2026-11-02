# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 推理引擎检查
#
# 清单只声明引擎在哪，不保证它还在。权重与引擎是两份独立交付物：升级
# 系统包可能换掉引擎路径，而清单仍是旧的。判定必须读一次磁盘，否则
# 状态会报就绪、服务一启动就崩在 execve 上。
#
# 只做只读检查，不执行引擎、不探测版本：版本探测要跑程序，可能阻塞或
# 触发驱动加载，这属于后端探测层，不属于本层。

"""AirymaxOS inference engine inspection (K-20-09).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
（运行期不发起网络请求、清单与实物不符即硬失败）。

"""

from __future__ import annotations

import stat
from pathlib import Path

from . import model
from .failure import Failure

__all__ = ["check"]

EXEC_BITS = 0o111


def check(entry: model.Manifest) -> None:
    """确认清单指定的引擎可执行；任何偏离都抛 :class:`Failure`。

    Args:
        entry: 已解析的清单。

    Raises:
        Failure: 引擎路径非绝对、不存在、非普通文件或无可执行位。
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
