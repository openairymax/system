# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 编码设置与转换面
#
# 分层约定：scan 只做 UTF-8 判定与码点计数，不读文件；conf 只解析与校验
# /etc/agentrt/charset.conf；cli 只分派子命令与输出。判定规则的唯一来源是
# docs-linux/180-i18n/02-encoding-spec.md，三处实现不各自维护一套判据。

"""AirymaxOS character encoding policy and conversion."""

__all__ = ["conf", "scan"]
