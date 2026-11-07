# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 维护者脚本的取舍与依赖报告
#
# deb 的 preinst/postinst/prerm/postrm 是 Debian 系 shell 脚本，直接塞进 RPM
# 会在错误的环境里执行（dpkg 变量、debconf、update-alternatives 都不在）。
# 默认拒绝，把脚本名与全文交给用户判断；只有显式 --ignore-scripts 才继续，
# 且仍要在安装前把全文打印出来，跳过必须是用户看见过的决定。
#
# Depends/Recommends 同样只报告：Debian 与 RPM 的包名没有稳定对应关系，
# 硬映射成 Requires 会让事务在名字对不上时直接失败，比缺依赖更难排查。

"""AirymaxOS deb install policy."""

from __future__ import annotations

from . import control
from .archive import Script

__all__ = ["blocked", "deps", "render"]


def blocked(found: tuple[Script, ...], allow: bool) -> bool:
    """有维护者脚本且未显式放行时拒绝。"""
    return bool(found) and not allow


def render(found: tuple[Script, ...]) -> str:
    """把脚本名与全文拼成一段，原样交给用户看。"""
    return "\n".join(_block(item) for item in found)


def _block(item: Script) -> str:
    return f"--- {item.name} ---\n{item.text.rstrip()}\n"


def deps(fields: dict[str, str]) -> tuple[str, ...]:
    """逐项列出 Depends/Recommends，供命令行打印。"""
    rows: list[str] = []
    for name in ("Depends", "Recommends"):
        rows.extend(f"{name}: {item}" for item in control.items(fields, name))
    return tuple(rows)
