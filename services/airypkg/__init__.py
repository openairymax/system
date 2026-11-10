# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 本地软件包安装入口的公开接口
#
# 只有一层分派：按后缀把 .deb 交给 airy-deb、.rpm 交给 dnf，确认与提权
# 复用 airydeb.interact。导出退出码与 Fault，供命令入口与门禁使用。

"""AirymaxOS local package install."""

from airydeb.err import (
    EXIT_BUILD,
    EXIT_ENV,
    EXIT_OK,
    EXIT_POLICY,
    EXIT_USAGE,
    Fault,
)

from .cli import main

__all__ = [
    "EXIT_BUILD",
    "EXIT_ENV",
    "EXIT_OK",
    "EXIT_POLICY",
    "EXIT_USAGE",
    "Fault",
    "main",
]
