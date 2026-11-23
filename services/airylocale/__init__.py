# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS locale 工具包
#
# 矩阵数据、校验与生成分层：matrix 只读数据文件并给出结构判定，cli 只做
# 命令行分派与证据输出。locale.conf 的解析同样集中在 matrix，避免「列出
# 支持项」和「校验已配置项」两处各写一套口径。

"""AirymaxOS locale matrix and generation helpers."""

from __future__ import annotations

__all__ = ["matrix"]
