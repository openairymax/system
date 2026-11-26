# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""AirymaxOS 开发运维工作站（P-18）。

八面板只读采集加受控操作：监控、日志、Agent、告警、事件、记忆、备份、
配置。权限按 view < operate < admin 单一排序表判定，admin 动作需要
--yes；远程与 Web 能力属于 v1.1.0，本版显式拒绝。
"""

from .model import (
    BadConfig,
    BadInput,
    BadPerm,
    BadState,
    BadStation,
    BadUnsupported,
    Panel,
    Section,
    Snapshot,
)

__version__ = "0.0.5"
__all__ = [
    "BadConfig",
    "BadInput",
    "BadPerm",
    "BadState",
    "BadStation",
    "BadUnsupported",
    "Panel",
    "Section",
    "Snapshot",
    "__version__",
]
