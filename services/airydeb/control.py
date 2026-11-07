# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# deb822 控制段落解析
#
# control.tar 里的 control 是 RFC822 风格段落：字段名大小写不敏感，续行以
# 空格或制表符开头并接到上一字段。这里只解析第一段，空行之后不再关心——
# deb 的 control 文件在标准里就是单段。
#
# 字段一律以小写键保存，调用方不必区分 Package/package。

"""AirymaxOS deb822 control parser."""

from __future__ import annotations

__all__ = ["field", "items", "parse"]


def parse(raw: str) -> dict[str, str]:
    """解析首个 control 段落，返回小写字段名到值的映射。"""
    fields: dict[str, str] = {}
    key = ""
    for line in raw.splitlines():
        if not line.strip():
            break
        key = _feed(line, fields, key)
    return fields


def _feed(line: str, fields: dict[str, str], key: str) -> str:
    if line[0] in " \t":
        fields[key] = f"{fields.get(key, '')} {line.strip()}".strip()
        return key
    name, _, value = line.partition(":")
    key = name.strip().lower()
    fields[key] = value.strip()
    return key


def field(fields: dict[str, str], name: str) -> str:
    """按名字取值，大小写不敏感。"""
    return fields.get(name.lower(), "")


def items(fields: dict[str, str], name: str) -> tuple[str, ...]:
    """把逗号分隔的 Depends/Recommends 拆成逐项。"""
    raw = field(fields, name)
    return tuple(part.strip() for part in raw.split(",") if part.strip())
