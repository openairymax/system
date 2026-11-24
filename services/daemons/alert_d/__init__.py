# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 告警聚合守护
#
# 模型、规则、判定、持久化、套接字服务与命令行分文件实现。包只导出事实
# 类型与规则加载入口，守护进程、命令行与测试因此共用同一份定义。

"""AirymaxOS alert aggregator (P-13)."""

from .model import Alert, AlertEvent, BadEvent, Severity
from .rules import RuleError, load_rules

__all__ = (
    "Alert",
    "AlertEvent",
    "BadEvent",
    "Severity",
    "RuleError",
    "load_rules",
)
