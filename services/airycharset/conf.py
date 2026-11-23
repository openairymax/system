# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""`/etc/agentrt/charset.conf` 解析与策略校验。

文件只承载「系统编码、允许入站的外源编码、BOM 与非字符策略、计数单位」
四类事实。策略取值收敛在此处，转换与判定读同一份结果，避免两处各写一套。
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from pathlib import Path

KEYS = ("CHARSET_ENC", "CHARSET_SOURCES", "CHARSET_BOM", "CHARSET_NONCHAR",
        "CHARSET_COUNT")
# 设计锚点 §3.1 以 GB18030 为入站示例，它因此是允许外源编码的最小集合。
REQUIRED_SOURCE = "GB18030"
MAX_SOURCE = 16
# 各键的固定取值与报错用语；CHARSET_ENC 允许大小写，单独判定。
EXACT = (("CHARSET_BOM", "reject", "BOM 策略"),
         ("CHARSET_NONCHAR", "reject", "非字符策略"),
         ("CHARSET_COUNT", "codepoint", "计数单位"))


@dataclass(frozen=True)
class Settings:
    enc: str
    sources: tuple[str, ...]
    bom: str
    nonchar: str
    count: str


def read(path: str | Path) -> dict[str, str]:
    """按 KEY=VALUE 读取，忽略空行与 # 之后的注释。"""
    raw: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        if "=" not in body:
            raise ValueError(f"缺少等号: {line!r}")
        key, val = body.split("=", 1)
        raw[key.strip()] = val.strip().strip('"')
    return raw


def _source_bad(name: str) -> bool:
    try:
        codecs.lookup(name)
    except LookupError:
        return True
    return False


def _sources(val: str) -> tuple[str, ...]:
    items = tuple(part.strip() for part in val.split(",") if part.strip())
    return items


def problems(raw: dict[str, str]) -> list[str]:
    """把违规项一次列全，供 conf 与门禁共用。"""
    return _keys_bad(raw) or _values_bad(raw)


def _keys_bad(raw: dict[str, str]) -> list[str]:
    return ([f"未知键: {key}" for key in raw if key not in KEYS]
            + [f"缺少键: {key}" for key in KEYS if key not in raw])


def _values_bad(raw: dict[str, str]) -> list[str]:
    out = [f"{label}必须是 {want}: {raw[key]}"
           for key, want, label in EXACT if raw[key] != want]
    if raw["CHARSET_ENC"].upper() != "UTF-8":
        out.insert(0, f"系统编码必须是 UTF-8: {raw['CHARSET_ENC']}")
    return out + _src_problems(_sources(raw["CHARSET_SOURCES"]))


def _src_problems(items: tuple[str, ...]) -> list[str]:
    if not items:
        return ["允许外源编码为空"]
    out: list[str] = []
    if len(items) > MAX_SOURCE:
        out.append(f"外源编码超过 {MAX_SOURCE} 条")
    if len({name.upper() for name in items}) != len(items):
        out.append("外源编码重复")
    out.extend(f"未知编码: {name}" for name in items if _source_bad(name))
    if REQUIRED_SOURCE.lower() not in {name.lower() for name in items}:
        out.append(f"缺少入站编码: {REQUIRED_SOURCE}")
    return out


def settings(raw: dict[str, str]) -> Settings:
    return Settings(
        enc=raw["CHARSET_ENC"].upper(),
        sources=_sources(raw["CHARSET_SOURCES"]),
        bom=raw["CHARSET_BOM"],
        nonchar=raw["CHARSET_NONCHAR"],
        count=raw["CHARSET_COUNT"],
    )
