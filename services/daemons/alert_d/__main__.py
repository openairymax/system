# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 告警聚合守护模块入口
#
# 供 `python3 -m alert_d` 使用；装机路径下由 airy-alertd 直接调用
# server.main()，两者走同一条启动路径。

"""Run the alert daemon as a module."""

import sys

from .server import main

if __name__ == "__main__":
    sys.exit(main())
