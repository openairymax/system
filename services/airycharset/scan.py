# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""UTF-8 判定与码点计数。

判定顺序与 `180-i18n/02-encoding-spec.md` §2.2 一致：首字节结构、续字节、
过长编码、码点上界、代理对、非字符、BOM。`measure` 一次遍历同时给出码点数
与首个问题，校验与计数因此不各扫一遍；调用方拿到的偏移足以定位坏序列。
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Optional

# 首字节区间下界与对应的序列长度：0x80..0xC1 与 0xF5 以上都不是合法首字节。
LEAD = (0x80, 0xC2, 0xE0, 0xF0, 0xF5)
SIZE = (1, 0, 2, 3, 4)
MIN_POINT = {2: 0x80, 3: 0x800, 4: 0x10000}
MAX_POINT = 0x10FFFF
BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True)
class Issue:
    """首个坏序列：偏移 + 机器可读类别 + 中文说明。"""

    pos: int
    code: str
    msg: str

    def text(self, path: str) -> str:
        return f"{path}:{self.pos}:{self.code}:{self.msg}"


def width(lead: int) -> int:
    """首字节声明的序列长度，非法首字节返回 0。"""
    if lead >= LEAD[-1]:
        return 0
    return SIZE[bisect.bisect_right(LEAD, lead)]


def point(seq: bytes) -> int:
    """把一个结构合法的序列还原成码点。"""
    if len(seq) == 1:
        return seq[0]
    cp = seq[0] & (0x7F >> len(seq))
    for byte in seq[1:]:
        cp = (cp << 6) | (byte & 0x3F)
    return cp


def _bad_byte(data: bytes, start: int, need: int) -> Optional[Issue]:
    """续字节数量或掩码不符。"""
    if start + need > len(data):
        return Issue(start, "invalid", "序列被截断")
    for k in range(1, need):
        if not 0x80 <= data[start + k] <= 0xBF:
            return Issue(start, "invalid", "续字节掩码不符")
    return None


def _banned(cp: int, start: int) -> Optional[Issue]:
    """代理对、字符串内 BOM 与非字符。"""
    if 0xD800 <= cp <= 0xDFFF:
        return Issue(start, "surrogate", "UTF-16 代理对")
    if cp == 0xFEFF:
        return Issue(start, "bom", "BOM 出现在字符串中")
    if (cp & 0xFFFE) == 0xFFFE or 0xFDD0 <= cp <= 0xFDEF:
        return Issue(start, "nonchar", "Unicode 非字符")
    return None


def _bad_point(cp: int, start: int, need: int) -> Optional[Issue]:
    """过长编码、码点上界与保留码点。"""
    if need > 1 and cp < MIN_POINT[need]:
        return Issue(start, "overlong", "过长编码")
    if cp > MAX_POINT:
        return Issue(start, "range", "码点超出 U+10FFFF")
    return _banned(cp, start)


def _check_one(data: bytes, start: int) -> tuple[int, Optional[Issue]]:
    need = width(data[start])
    if not need:
        return 0, Issue(start, "invalid", "非法首字节")
    bad = _bad_byte(data, start, need)
    if bad:
        return 0, bad
    return need, _bad_point(point(data[start:start + need]), start, need)


def measure(data: bytes) -> tuple[int, Optional[Issue]]:
    """一次遍历：合法时返回 (码点数, None)，否则 (0, 首个问题)。"""
    pos = total = 0
    while pos < len(data):
        need, bad = _check_one(data, pos)
        if bad:
            return 0, bad
        pos += need
        total += 1
    return total, None


def validate(data: bytes) -> Optional[Issue]:
    """整串校验，合法返回 None，否则返回首个问题。"""
    return measure(data)[1]


def count(data: bytes) -> int:
    """已校验输入上的码点计数（Token 预算按码点，不按字节）。"""
    return measure(data)[0]


def has_bom(data: bytes) -> bool:
    return data.startswith(BOM)
