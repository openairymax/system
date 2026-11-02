# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 升级通道服务包
#
# 版本发现、基底勘误与状态读写的命名空间入口。对外只导出数据契约，
# 网络取件与事务执行不因导入本包而生效。

"""AirymaxOS update channel package (V-14).

设计依据：docs-linux/190-distribution/04-update-mechanism.md §2。
"""

from __future__ import annotations

from .identity import Verdict, compare
from .model import (
    CHANNELS,
    CHANNEL_STABLE,
    EXIT_FAIL,
    EXIT_NEW,
    EXIT_OK,
    EXIT_REINSTALL,
    FEED_URL,
    FIELDS,
    ISO_FIELDS,
    ISO_OPTIONAL,
    KERNEL_FIELDS,
    NOTES_HOSTS,
    SCHEMA_VERSION,
    STATE_DIR,
    STATE_FILE,
    Asset,
    Identity,
    Kernel,
    Manifest,
)

__all__ = [
    "CHANNELS",
    "CHANNEL_STABLE",
    "EXIT_FAIL",
    "EXIT_NEW",
    "EXIT_OK",
    "EXIT_REINSTALL",
    "FEED_URL",
    "FIELDS",
    "ISO_FIELDS",
    "ISO_OPTIONAL",
    "KERNEL_FIELDS",
    "NOTES_HOSTS",
    "SCHEMA_VERSION",
    "STATE_DIR",
    "STATE_FILE",
    "Asset",
    "Identity",
    "Kernel",
    "Manifest",
    "Verdict",
    "compare",
]
