# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 命令行共用层
#
# 退出码与失败类型只定义一次（err），分派与输出只实现一次（cli）；各工具的
# 命令行从这里取，不再各写一份数字与 try/except。

"""AirymaxOS command line shared protocol."""

__all__ = ["cli", "err"]
