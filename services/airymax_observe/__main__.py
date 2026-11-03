# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 硬件清点的模块入口
#
# airy-observe 是 AirymaxOS 唯一的观察命令，由发行包安装；它的 hardware
# 子命令转交给本模块。用 python3 -m 而不是再装一个同名或近名的可执行文件：
# 命令名已经有主，硬件清点只是它的一种视角。

"""AirymaxOS hardware observation as ``python3 -m airymax_observe``."""

from __future__ import annotations

import sys

from . import main

sys.exit(main())