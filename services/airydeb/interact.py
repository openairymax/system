# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 终端确认与提权
#
# 这两件事在 deb 与 rpm 两条安装路径上必须逐字一致：桌面双击进来的会话
# 与终端里敲命令的会话，看到的问句、认的答案、走的提权入口都只能有一份，
# 否则「终端里能装、桌面上装不了」这类漂移只会在用户现场出现。
#
# 确认只认显式肯定：y/yes 才继续，空行、EOF 与其他任何输入都按取消处理，
# 且取消不产生副作用。没有终端时直接拒绝而不是默认继续——桌面分派误把
# 无终端上下文传进来时，宁可失败也不静默改系统。
#
# 提权只走 pkexec 重入同一入口：不做「桌面调一套、终端调另一套」的第二
# 实现。入口在 PATH 里找不到时报环境缺失，并给出 sudo 的等价命令。

"""AirymaxOS package install interaction."""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Sequence

from .err import EXIT_ENV, EXIT_POLICY, Fault

__all__ = ["ask", "elevate"]

# pkexec 的 126/127 分别是「授权被撤销/未获授权」与「授权不可用」，两者
# 在用户眼里都是「没提权成功」，归到策略拒绝，不与环境缺失混在一起。
DENIED = (126, 127)


def ask(prompt: str) -> bool:
    """读一行确认；只认 y/yes（大小写不敏感），其余一律取消。"""
    if not sys.stdin.isatty():
        raise Fault("需要在终端确认，当前没有终端，已拒绝继续", EXIT_POLICY)
    try:
        reply = input(prompt).strip().lower()
    except EOFError:
        return False
    return reply in ("y", "yes")


def elevate(entry: str, argv: Sequence[str]) -> int:
    """以 pkexec 重入 <entry>，返回其退出码；授权失败归为策略拒绝。"""
    tool = shutil.which("pkexec")
    if tool is None:
        raise Fault(f"缺少 pkexec，无法提权；请用 sudo {entry} 重试", EXIT_ENV)
    target = shutil.which(entry)
    if target is None:
        raise Fault(f"PATH 中没有 {entry}，无法提权重入；请用 sudo {entry}",
                    EXIT_ENV)
    code = subprocess.run([tool, target, *argv], check=False).returncode
    if code in DENIED:
        print(f"{entry}: pkexec 返回 {code}（未获授权或已取消）",
              file=sys.stderr)
        return EXIT_POLICY
    return code
