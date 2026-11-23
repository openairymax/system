# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# localedef 的定位、归档清单与调用
#
# 生成与落地判定都要问同一组外部事实：工具在哪、locale-archive 里有什么。
# 把它们收在一处，命令行不再直接持有工具名与子进程细节。

"""localedef invocation used by the locale command line."""

from __future__ import annotations

import shutil
import subprocess
import sys

from airylib.err import EXIT_ENV, EXIT_RUN, Fault

TOOL = "localedef"

__all__ = ["TOOL", "archive", "execute", "path"]


def path() -> str | None:
    """localedef 的绝对路径；未安装时为 None。"""
    return shutil.which(TOOL)


def archive(tool: str) -> str:
    """locale-archive 中的条目清单；非零退出时为空串。"""
    try:
        proc = subprocess.run([tool, "--list-archive"], capture_output=True,
                              text=True, check=False)
    except OSError:
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def execute(argv: list[str]) -> int:
    """执行一条 localedef 命令并返回退出码；工具缺失按环境问题上报。"""
    found = shutil.which(argv[0])
    if not found:
        raise Fault("缺少 localedef", EXIT_ENV)
    try:
        return subprocess.run([found, *argv[1:]], check=False).returncode
    except OSError as exc:
        print(f"{argv[0]}: {exc}", file=sys.stderr)
        return EXIT_RUN
