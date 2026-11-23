# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 本地软件包安装入口的公开接口
#
# 只有一层分派：按后缀把 .deb 交给 airy-deb、.rpm 交给 dnf，确认与提权
# 复用 airydeb.interact。

"""AirymaxOS local package install."""

from .cli import main

__all__ = [
    "main",
]
