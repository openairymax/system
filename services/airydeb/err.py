# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 退出码与可预期失败
#
# 一处定义，archive/plan/spec/cli 共用，避免各模块各写一套数字。异常自带
# 退出码，命令行只负责打印与返回，不再按异常类型二次分派。

"""AirymaxOS deb helper failures and exit codes."""

from __future__ import annotations

__all__ = [
    "EXIT_BUILD",
    "EXIT_ENV",
    "EXIT_OK",
    "EXIT_POLICY",
    "EXIT_USAGE",
    "Fault",
]

EXIT_OK = 0

EXIT_USAGE = 2

EXIT_ENV = 4

EXIT_POLICY = 5

EXIT_BUILD = 6


class Fault(Exception):
    """一次可预期的失败；code 即进程退出码。"""

    def __init__(self, message: str, code: int = EXIT_ENV) -> None:
        super().__init__(message)
        self.code = code
