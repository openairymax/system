# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 命令行共用层
#
# 退出码与失败类型只定义一次（err），分派与输出只实现一次（cli），台账的
# 紧凑 JSON 与原子落盘只实现一次（ledger）；各工具从这里取，不再各写一份
# 数字、try/except 或 json.dumps。

"""AirymaxOS command line shared protocol."""

__all__ = ["cli", "err", "ledger"]
