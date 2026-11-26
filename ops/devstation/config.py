# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""工作站配置：devstation.* 八项，全部带默认值与取值范围校验。

配置缺失时使用默认值；任何一项越界或无法解析都抛 BadConfig，由命令行
映射为退出码 3。未知的 devstation.* 键同样拒绝，避免拼写错误被静默吃掉。
"""

import os

from .model import BadConfig, PANELS, RANK, need

CONF = "/etc/agentrt/devstation.conf"
ENV = "AIRY_STATION_CONF"
PREFIX = "devstation."

DEFAULTS = {
    "devstation.refresh_interval_s": 2,
    "devstation.color_scheme": "dark",
    "devstation.default_panel": "monitor",
    "devstation.web_enabled": False,
    "devstation.web_port": 8610,
    "devstation.permission_level": "view",
    "devstation.log_buffer_lines": 500,
    "devstation.alert_buffer_size": 100,
}

KEYS = tuple(DEFAULTS)
BOOLS = {"true": True, "yes": True, "on": True, "1": True,
         "false": False, "no": False, "off": False, "0": False}


def _bool(text: str) -> bool:
    got = BOOLS.get(text.strip().lower())
    if got is None:
        raise BadConfig(f"布尔值无法解析: {text}")
    return got


def _int(text: str, low: int, high: int, key: str) -> int:
    try:
        got = int(text.strip(), 10)
    except ValueError as exc:
        raise BadConfig(f"{key} 不是整数: {text}") from exc
    if got < low or got > high:
        raise BadConfig(f"{key} 越界: {got} 不在 [{low}, {high}]")
    return got


def _text(text: str, allowed, key: str) -> str:
    got = text.strip().strip('"').strip("'")
    if got not in allowed:
        raise BadConfig(f"{key} 取值非法: {got}")
    return got


def validate(values) -> dict:
    """合并默认值并逐项校验，返回完整配置。"""
    out = dict(DEFAULTS)
    for key, value in values.items():
        if key.startswith(PREFIX):
            need(key in DEFAULTS, f"未知配置项: {key}", BadConfig)
            out[key] = value

    out["devstation.refresh_interval_s"] = _int(
        str(out["devstation.refresh_interval_s"]), 1, 86400,
        "devstation.refresh_interval_s",
    )
    out["devstation.log_buffer_lines"] = _int(
        str(out["devstation.log_buffer_lines"]), 10, 100000,
        "devstation.log_buffer_lines",
    )
    out["devstation.alert_buffer_size"] = _int(
        str(out["devstation.alert_buffer_size"]), 1, 100000,
        "devstation.alert_buffer_size",
    )
    out["devstation.web_port"] = _int(
        str(out["devstation.web_port"]), 1, 65535, "devstation.web_port"
    )
    if not isinstance(out["devstation.web_enabled"], bool):
        out["devstation.web_enabled"] = _bool(
            str(out["devstation.web_enabled"])
        )
    out["devstation.color_scheme"] = _text(
        str(out["devstation.color_scheme"]), ("dark", "light"),
        "devstation.color_scheme",
    )
    out["devstation.default_panel"] = _text(
        str(out["devstation.default_panel"]),
        tuple(panel.value for panel in PANELS),
        "devstation.default_panel",
    )
    out["devstation.permission_level"] = _text(
        str(out["devstation.permission_level"]), tuple(RANK),
        "devstation.permission_level",
    )
    return out


def load(path=None) -> dict:
    """读取并校验配置；文件不存在时返回全默认值。"""
    target = path or os.environ.get(ENV) or CONF
    values = {}
    if target and os.path.exists(target):
        try:
            with open(target, encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError as exc:
            raise BadConfig(f"配置不可读: {target}: {exc}") from exc
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise BadConfig(f"配置行缺少等号: {line}")
            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith(PREFIX):
                values[key] = value.strip()
    return validate(values)
