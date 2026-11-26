# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 记忆运维配置
#
# 职责：读取 /etc/agentrt/memory.conf（可被 --conf 或 AIRY_MEMORY_CONF 覆盖），
# 支持 `airy_memory.key = value` 与 `key: value` 两种写法；默认值与设计 §10
# 表格逐项一致，越界取值在加载阶段即判负。
#
# 边界：只认 airy_memory.* 前缀的键，其余键静默忽略（同文件由其它组件共用）；
# 不写配置、不做热加载——配置是启动输入，不是运行时状态。
#
# 依赖：model.py 的窗口与方法校验、标准库 pathlib/os。

"""AirymaxOS memory configuration loader."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from airylib.err import need

from .model import BadConfig, Method, check_window

__all__ = ["DEFAULTS", "CONF", "load"]

CONF = "/etc/agentrt/memory.conf"
PREFIX = "airy_memory."

DEFAULTS: Dict[str, Any] = {
    "l1_default_mb": 256,
    "l1_max_mb": 512,
    "l2_default_mb": 1024,
    "l2_max_mb": 2048,
    "l3_block_kb": 4096,
    "l3_index_cache_mb": 16,
    "l4_compress_level": 19,
    "defrag_threshold_pct": 30,
    "defrag_window": "02:00-05:00",
    "defrag_max_duration_min": 30,
    "erase_default_level": "secure",
    "erase_audit": True,
    "shared_l2_enabled": True,
}

_TRUTHY = ("1", "true", "yes", "on")
_FALSY = ("0", "false", "no", "off")


def _value(name: str, text: str) -> Any:
    want = DEFAULTS[name]
    raw = text.strip()
    if isinstance(want, bool):
        need(raw.lower() in _TRUTHY + _FALSY,
             f"配置 {PREFIX}{name} 不是布尔值: {text!r}", BadConfig)
        return raw.lower() in _TRUTHY
    if isinstance(want, int):
        try:
            return int(raw, 0)
        except ValueError as exc:
            raise BadConfig(
                f"配置 {PREFIX}{name} 不是整数: {text!r}") from exc
    return raw


def _pair(line: str, where: str) -> Any:
    for mark in ("=", ":"):
        if mark in line:
            key, text = line.split(mark, 1)
            return key.strip(), text.strip()
    raise BadConfig(f"{where} 不是 key=value 或 key: value")


def _apply(values: Dict[str, Any], key: str, text: str) -> None:
    name = key[len(PREFIX):] if key.startswith(PREFIX) else key
    if name in DEFAULTS:
        values[name] = _value(name, text)


def _validate(values: Dict[str, Any]) -> None:
    for name in ("l1_default_mb", "l1_max_mb", "l2_default_mb", "l2_max_mb",
                 "l3_block_kb", "l3_index_cache_mb", "defrag_max_duration_min"):
        need(isinstance(values[name], int) and values[name] > 0,
             f"配置 {PREFIX}{name} 必须为正整数", BadConfig)
    need(values["l1_default_mb"] <= values["l1_max_mb"],
         "l1_default_mb 不得超过 l1_max_mb", BadConfig)
    need(values["l2_default_mb"] <= values["l2_max_mb"],
         "l2_default_mb 不得超过 l2_max_mb", BadConfig)
    need(1 <= values["l4_compress_level"] <= 19,
         "l4_compress_level 必须在 1..19", BadConfig)
    need(0 <= values["defrag_threshold_pct"] <= 100,
         "defrag_threshold_pct 必须在 0..100", BadConfig)
    check_window(values["defrag_window"])
    need(values["erase_default_level"] in {member.value for member in Method},
         f"erase_default_level 不合法: {values['erase_default_level']!r}",
         BadConfig)


def load(path: Optional[str] = None) -> Dict[str, Any]:
    target = Path(path or os.environ.get("AIRY_MEMORY_CONF") or CONF)
    values = dict(DEFAULTS)
    if target.is_file():
        rows = target.read_text(encoding="utf-8").splitlines()
        for number, raw in enumerate(rows, 1):
            line = raw.strip()
            if line and line[0] not in "#;":
                key, text = _pair(line, f"{target}:{number}")
                _apply(values, key, text)
    _validate(values)
    return values
