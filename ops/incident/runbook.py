# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 复盘报告与运行手册
#
# 复盘报告按运维分册第 8.1 节的模板生成，能从台账算出来的字段一律
# 自动填，只有需要人来判断的段落留占位。运行手册是随包安装的静态
# 文档，命令行只负责按名字取用，不在代码里另存一份副本。

"""Postmortem rendering and runbook lookup."""

from __future__ import annotations

from pathlib import Path

from .model import NS, BadInput, BadStore, Incident, iso, parse_iso

SHARE = Path("/usr/share/airymax/incident/runbooks")
PREFIX = "INC-"
REPORT = "PM-"


def folder() -> Path:
    for path in (Path(__file__).resolve().with_name("runbooks"), SHARE):
        if path.is_dir():
            return path
    raise BadStore("找不到 runbook 目录")


def names() -> list[str]:
    return sorted(path.stem for path in folder().glob("*.md"))


def text(name: str) -> str:
    body = str(name or "").strip()
    if body not in names():
        raise BadStore(f"未知 runbook: {name}")
    return (folder() / f"{body}.md").read_text(encoding="utf-8")


def report_id(inc: Incident) -> str:
    return REPORT + inc.incident_id[len(PREFIX):]


def span_text(stamp: int) -> str:
    secs = max(stamp, 0) // NS
    days, rest = divmod(secs, 86400)
    hours, rest = divmod(rest, 3600)
    mins, secs = divmod(rest, 60)
    body = f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{days}d {body}" if days else body


def _clock(inc: Incident, row: dict) -> str:
    try:
        stamp = parse_iso(str(row.get("ts") or ""))
    except BadInput:
        return "T+?"
    return f"T+{span_text(stamp - inc.detected_at)}"


def _line(inc: Incident, row: dict) -> str:
    event = str(row.get("event") or "?")
    actor = str(row.get("actor") or "?")
    detail = str(row.get("detail") or "").replace("|", "/")
    body = f"{event} {detail}".strip()
    return f"| {_clock(inc, row)} | {body} | {actor} |"


def _rows(inc: Incident) -> str:
    body = "\n".join(_line(inc, row) for row in inc.timeline)
    return body or "| T+00:00:00 | 无 | - |"


def _list(values: list) -> str:
    return "、".join(str(item) for item in values) if values else "无"


def _span(inc: Incident) -> str:
    if not inc.resolved_at:
        return "未恢复"
    return span_text(inc.resolved_at - inc.detected_at)


def render(inc: Incident, by: str, stamp: int, summary: str = "",
           root_cause: str = "") -> str:
    """按模板渲染一份复盘报告，未定的判断留占位。"""
    cause = root_cause.strip() or inc.root_cause
    lines = [
        f"# 事件复盘报告：{inc.title}",
        "",
        "## 元信息",
        f"- 事件 ID：{inc.incident_id}",
        f"- 复盘 ID：{report_id(inc)}",
        f"- 严重程度：{inc.severity.value}",
        f"- 类别：{inc.category.value}",
        f"- 检测时间：{iso(inc.detected_at)}",
        f"- 恢复时间：{iso(inc.resolved_at) if inc.resolved_at else '未恢复'}",
        f"- 持续时长：{_span(inc)}",
        f"- 报告人：{by}",
        f"- 报告时间：{iso(stamp)}",
        "",
        "## 1. 事件摘要",
        summary.strip() or (
            f"{inc.title}；持续 {_span(inc)}，影响 {len(inc.affected_agents)}"
            f" 个 Agent、{len(inc.affected_daemons)} 个 daemon。"),
        "",
        "## 2. 影响分析",
        f"- 受影响 Agent：{_list(inc.affected_agents)}",
        f"- 受影响 daemon：{_list(inc.affected_daemons)}",
        "- 业务影响：<待补>",
        "- 数据影响：<待补>",
        "",
        "## 3. 时间线",
        "| 时间 | 事件 | 来源 |",
        "|------|------|------|",
        _rows(inc),
        "",
        "## 4. 根因分析",
        "触发原因见 4.1，根本原因见 4.2，追问过程见 4.3。",
        "",
        "### 4.1 触发原因",
        "<待补：直接触发条件与传导路径>",
        "",
        "### 4.2 根本原因",
        cause or "<待补：设计缺陷、配置错误或外部依赖>",
        "",
        "### 4.3 5 Whys 分析",
        "1. 为什么发生？→ <待补>",
        "2. 为什么上一步成立？→ <待补>",
        "3. 为什么上一步成立？→ <待补>",
        "4. 为什么上一步成立？→ <待补>",
        "5. 为什么上一步成立？→ <待补：追到流程或设计层>",
        "",
        "## 5. 响应评估",
        "- 检测是否及时？<是/否，说明>",
        "- 分类是否准确？<是/否，说明>",
        "- 自动响应是否有效？<是/否，说明>",
        "- 人工介入是否及时？<是/否，说明>",
        "- 恢复是否完整？<是/否，说明>",
        "",
        "## 6. 改进措施",
        "| 编号 | 措施 | 负责人 | 截止日期 | 状态 |",
        "|------|------|--------|----------|------|",
        "| 1    | <待补> | <姓名> | <日期>   | 待办 |",
        "",
        "## 7. 经验教训",
        "- <待补>",
        "",
        "## 8. 附录",
        f"- 台账记录：{inc.incident_id}.json",
        f"- 关联告警：{_list(inc.alerts)}",
        f"- 处置动作：{len(inc.response_actions)} 条",
        "",
    ]
    return "\n".join(lines)
