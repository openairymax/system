# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""开发运维工作站的数据模型：八类面板、快照与权限排序。

权限只有一张排序表 RANK，任何可见性与动作都从它派生，避免出现
「面板放行、动作拒绝」这类两套标准。异常码与命令行退出码一一对应。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List

EXIT_USAGE = 2
EXIT_STATE = 3
EXIT_ENV = 4

RANK = {"view": 0, "operate": 1, "admin": 2}


class Panel(str, Enum):
    """八类只读面板；顺序即监控页的切换顺序。"""

    MONITOR = "monitor"
    LOGS = "logs"
    AGENTS = "agents"
    ALERTS = "alerts"
    INCIDENTS = "incidents"
    MEMORY = "memory"
    BACKUP = "backup"
    CONFIG = "config"


PANELS = tuple(Panel)
HOTKEY = {str(index + 1): panel for index, panel in enumerate(PANELS)}
TITLE = {
    Panel.MONITOR: "监控",
    Panel.LOGS: "日志",
    Panel.AGENTS: "Agent",
    Panel.ALERTS: "告警",
    Panel.INCIDENTS: "事件",
    Panel.MEMORY: "记忆",
    Panel.BACKUP: "备份",
    Panel.CONFIG: "配置",
}

ACTION = {
    "agent.start": "operate",
    "agent.pause": "operate",
    "agent.resume": "operate",
    "agent.stop": "operate",
    "agent.restart": "operate",
    "memory.erase": "admin",
}


class BadStation(Exception):
    """工作站异常的公共基类。"""

    code = EXIT_STATE


class BadInput(BadStation):
    """参数不合法。"""

    code = EXIT_USAGE


class BadState(BadStation):
    """状态或数据源不允许当前操作。"""

    code = EXIT_STATE


class BadPerm(BadStation):
    """当前权限级别不足或缺少二次确认。"""

    code = EXIT_ENV


class BadConfig(BadStation):
    """配置项缺失、越界或无法解析。"""

    code = EXIT_STATE


class BadUnsupported(BadStation):
    """能力属于后续版本，必须显式拒绝而不是静默忽略。"""

    code = EXIT_STATE


def need(cond: Any, msg: str, kind: type = BadInput) -> None:
    """断言表达式为真，否则抛指定异常。"""
    if not cond:
        raise kind(msg)


def rank_of(level: str) -> int:
    """返回权限级别的唯一序号。"""
    if level not in RANK:
        raise BadInput(f"未知权限级别: {level}")
    return RANK[level]


def need_perm(level: str, action: str) -> None:
    """校验权限级别足以执行动作。"""
    if action not in ACTION:
        raise BadInput(f"未知动作: {action}")
    if rank_of(level) < rank_of(ACTION[action]):
        raise BadPerm(
            f"权限不足: {level} < {ACTION[action]}（动作 {action}）"
        )


def parse_panel(name: str) -> Panel:
    """把面板名解析为枚举，非法名退出 2。"""
    try:
        return Panel(name)
    except ValueError as exc:
        raise BadInput(f"未知面板: {name}") from exc


def now_ns() -> int:
    """当前时间，纳秒。"""
    import time

    return time.time_ns()


def iso(stamp: int) -> str:
    """纳秒时间戳转 UTC ISO-8601，秒级。"""
    import datetime

    sec = stamp / 1_000_000_000
    return datetime.datetime.fromtimestamp(
        sec, datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Section:
    """单个面板的采集结果；缺源时 status=unavailable。"""

    name: str
    status: str = "ok"
    reason: str = ""
    rows: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """数据源可用。"""
        return self.status == "ok"

    def to_json(self) -> Dict[str, Any]:
        """序列化为可被 snapshot 消费的字典。"""
        return {
            "status": self.status,
            "reason": self.reason,
            "rows": self.rows,
        }


@dataclass
class Snapshot:
    """一次完整采集的快照，供渲染与机器消费共用。"""

    stamp: int
    node: str
    level: str
    sections: Dict[str, Section]

    def to_json(self) -> Dict[str, Any]:
        """输出稳定字段顺序的快照字典。"""
        return {
            "schema": 1,
            "stamp": iso(self.stamp),
            "node": self.node,
            "level": self.level,
            "sections": {
                panel.value: self.sections[panel.value].to_json()
                for panel in PANELS
                if panel.value in self.sections
            },
        }

    def section(self, name: str) -> Section:
        """取面板分节；未采集的面板按 unavailable 处理。"""
        got = self.sections.get(name)
        if got is None:
            return Section(name=name, status="unavailable", reason="未采集")
        return got
