# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS Agent 运维
#
# 生命周期、健康评估、资源调优与日志读取的落地实现。Agent 元数据落在
# /var/lib/agentrt/agents/<id>/meta.json，入口 bin/airy-agent 只做参数
# 解析与退出码映射，设计文档中的 airyctl agent 子树由本包承担。

"""Agent operations for AirymaxOS."""

__version__ = "0.0.5"
