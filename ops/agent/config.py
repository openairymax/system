# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS Agent 运维默认配置
#
# 设计 §10 的 13 个默认值集中在这里，命令行与调优逻辑只从 Config 取值，
# 不在各模块散落第二份数字。默认配置文件为 /etc/agentrt/agents.conf，
# 键可带 airy_agent. 前缀，形如 `default_l1_mb = 256` 或
# `airy_agent.default_l1_mb: 256`；环境变量 AIRY_AGENT_CONF 可改路径。

"""Central defaults and the /etc/agentrt override loader."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .model import BadConfig

CONF = "/etc/agentrt/agents.conf"
PREFIX = "airy_agent."

DEFAULTS: dict[str, Any] = {
    "heartbeat_interval_s": 2,
    "heartbeat_timeout_s": 6,
    "deep_check_interval_s": 30,
    "auto_restart_burst": 3,
    "auto_migrate_on_failure": True,
    "default_l1_mb": 256,
    "default_l2_mb": 1024,
    "default_token_budget": 1000000,
    "default_sched_runtime_ms": 10,
    "default_sched_deadline_ms": 30,
    "default_sched_period_ms": 100,
    "log_retention_d": 30,
    "version_rollback_keep": 3,
}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class Config:
    """Effective defaults plus the file they came from."""

    def __init__(self, values: dict[str, Any], source: str) -> None:
        self.values = values
        self.source = source

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def to_json(self) -> dict[str, Any]:
        return {"source": self.source, "values": dict(self.values)}


def _flag(text: str, key: str) -> bool:
    low = text.strip().lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    raise BadConfig(f"配置项 {key} 需要布尔值: {text!r}")


def _value(key: str, text: str) -> Any:
    if isinstance(DEFAULTS[key], bool):
        return _flag(text, key)
    try:
        return int(text.strip(), 0)
    except ValueError as exc:
        raise BadConfig(f"配置项 {key} 需要整数: {text!r}") from exc


def load(path: str | None = None) -> Config:
    target = Path(path or os.environ.get("AIRY_AGENT_CONF") or CONF)
    values = dict(DEFAULTS)
    if not target.is_file():
        return Config(values, str(target))
    for number, raw in enumerate(
            target.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if "=" in line:
            key, text = line.split("=", 1)
        elif ":" in line:
            key, text = line.split(":", 1)
        else:
            raise BadConfig(f"{target}:{number} 不是 key=value")
        key = key.strip()
        if key.startswith(PREFIX):
            key = key[len(PREFIX):]
        if key not in DEFAULTS:
            continue
        values[key] = _value(key, text)
    return Config(values, str(target))
