# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 以模块方式运行事件台账命令行，便于在无安装路径时直接调用。

"""Run the incident ledger command line as a module."""

import sys

from .cli import main

raise SystemExit(main())
