# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 一次性通知的公共落地
#
# 低电量告警与内存压力告警是同一形状：把等级写进 /run，等级变化时调用一次
# 广播，等级未变不重复。两处各写一遍会把「何谓一次」的判据分叉，一处按
# 字符串比较、另一处按枚举比较，最终表现为同一次故障刷屏或缺报。故收敛到
# 本文件，调用方只给等级名与消息。
#
# 命令失败不影响状态写入：通知不是动作的前提，动作本身由调用方保证幂等；
# 反过来，状态写不进去也不能让命令重复执行。两条都失败时静默，因为这里
# 已无更可靠的通道可用，噪音只会污染日志。

"""AirymaxOS one-shot notices."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

__all__ = ["call", "last", "store"]


def last(path: str) -> str:
    """读取上次通知的等级名；文件缺失或不可读返回空串。"""
    try:
        return Path(path).read_text(encoding="ascii").strip()
    except OSError:
        return ""


def store(path: str, value: str) -> None:
    """写入本次通知的等级名；写不进去时放弃，不影响调用方继续判定。"""
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value + "\n", encoding="ascii")
    except OSError:
        pass


def call(command: str, args: Sequence[str]) -> None:
    """执行一条通知或动作命令；超时与不存在都静默忽略。"""
    try:
        subprocess.run([command, *args], capture_output=True, check=False,
                       timeout=15)
    except (OSError, subprocess.SubprocessError):
        pass
