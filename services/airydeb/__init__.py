# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# Debian 包转换与安装包的公开接口
#
# 四份职责分开：archive 只认 ar/tar 结构并保证解包不越界，control 只认
# deb822 段落，spec 只把已解包的树翻成配方，plan 只决定维护者脚本能否跳过。
# 外部通常只用 main 与三个只读动作，其余导出供测试与门禁直接调用。

"""AirymaxOS Debian package conversion."""

from .archive import (
    Deb,
    Member,
    Script,
    control_text,
    open_deb,
    scan,
    scripts,
    unpack,
)
from .cli import main
from .control import field, items, parse
from .err import (
    EXIT_BUILD,
    EXIT_ENV,
    EXIT_OK,
    EXIT_POLICY,
    EXIT_USAGE,
    Fault,
)
from .plan import blocked, deps, render
from .spec import ARCHS, entries, legal, meta, text, upstream

__all__ = [
    "ARCHS",
    "Deb",
    "EXIT_BUILD",
    "EXIT_ENV",
    "EXIT_OK",
    "EXIT_POLICY",
    "EXIT_USAGE",
    "Fault",
    "Member",
    "Script",
    "blocked",
    "control_text",
    "deps",
    "entries",
    "field",
    "items",
    "legal",
    "main",
    "meta",
    "open_deb",
    "parse",
    "render",
    "scan",
    "scripts",
    "text",
    "unpack",
    "upstream",
]
