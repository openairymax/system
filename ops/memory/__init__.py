# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 记忆运维
#
# L3/L4 记忆仓库的查看、碎片整理、迁移/克隆与安全擦除实现。设计文档里的
# airyctl memory 子树由本包承担，入口 bin/airy-memory 只做参数解析与
# 退出码映射；运行时仓库落在 /var/lib/airy/memory。

"""Memory operations for AirymaxOS."""

__version__ = "0.0.5"
