# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS Agent 健康评分
#
# 六因素权重固定为 30/15/15/15/15/10（设计 §3.2）。数据只取真实来源：
# 心跳超时次数取自 meta.json 计数，CPU 与 RSS 取自 /proc/<pid>/，IPC P99
# 取自 meta.json 由宏控写入的采样，错误数取自当日日志中最近 60 秒的
# ERROR/FATAL 行，令牌余量取自 meta.json 预算。任一来源缺失即标记
# unknown 并按已知权重重新归一，同时给出 coverage_pct；覆盖不足 50%
# 时等级记为 UNKNOWN，绝不用缺省数值冒充健康。

"""Six-factor health scoring backed by /proc and stored counters."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import logs
from .model import NS, Agent

WEIGHTS = {
    "heartbeat": 30,
    "cpu": 15,
    "memory": 15,
    "ipc": 15,
    "errors": 15,
    "token": 10,
}
MIN_COVER = 50.0


def _btime() -> float:
    for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
        if line.startswith("btime "):
            return float(line.split()[1])
    raise ValueError("缺少 btime")


def _rss(pid: int) -> float | None:
    text = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return None


def read_proc(pid: int | None, stamp: int) -> dict[str, float | None] | None:
    if not pid:
        return None
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        parts = text[text.rfind(")") + 2:].split()
        utime, stime, start = (int(parts[11]), int(parts[12]),
                               int(parts[19]))
        hz = os.sysconf("SC_CLK_TCK")
        elapsed = stamp / NS - (_btime() + start / hz)
        cpu = (utime + stime) / hz / elapsed * 100 if elapsed > 0 else None
        return {"cpu_pct": cpu, "rss_mb": _rss(pid)}
    except (OSError, ValueError, IndexError):
        return None


def _share(usage: float) -> float:
    return 100.0 if usage <= 90.0 else max(0.0, 100.0 - (usage - 90.0))


def _factor(name: str, weight: int, score: float, source: str,
            detail: str) -> dict[str, Any]:
    return {"name": name, "weight": weight, "known": True,
            "score": round(min(100.0, max(0.0, score)), 3),
            "source": source, "detail": detail}


def _unknown(name: str, weight: int, source: str) -> dict[str, Any]:
    return {"name": name, "weight": weight, "known": False, "score": None,
            "source": source, "detail": "无可用数据"}


def _heartbeat(agent: Agent) -> dict[str, Any]:
    count = agent.heartbeat_timeouts
    if count is None:
        return _unknown("heartbeat", WEIGHTS["heartbeat"],
                        "meta.heartbeat_timeouts")
    return _factor("heartbeat", WEIGHTS["heartbeat"],
                   100.0 - count * 100.0 / 30.0,
                   "meta.heartbeat_timeouts", f"超时 {count} 次")


def _cpu(agent: Agent, proc: dict[str, Any] | None) -> dict[str, Any]:
    source = f"/proc/{agent.pid}/stat"
    if proc is None or proc.get("cpu_pct") is None:
        return _unknown("cpu", WEIGHTS["cpu"], source)
    quota = agent.sched["runtime_ms"] * 100.0 / agent.sched["period_ms"]
    usage = proc["cpu_pct"] / quota * 100.0
    return _factor("cpu", WEIGHTS["cpu"], _share(usage), source,
                   f"占配额 {usage:.1f}%")


def _memory(agent: Agent, proc: dict[str, Any] | None) -> dict[str, Any]:
    source = f"/proc/{agent.pid}/status"
    if proc is None or proc.get("rss_mb") is None:
        return _unknown("memory", WEIGHTS["memory"], source)
    limit = agent.memory["l1_mb"] + agent.memory["l2_mb"]
    usage = proc["rss_mb"] * 100.0 / limit
    return _factor("memory", WEIGHTS["memory"], _share(usage), source,
                   f"RSS {proc['rss_mb']:.1f}MB / {limit}MB")


def _ipc(agent: Agent) -> dict[str, Any]:
    value = agent.ipc_p99_ms
    if value is None:
        return _unknown("ipc", WEIGHTS["ipc"], "meta.ipc_p99_ms")
    score = 100.0 if value <= 5.0 else 100.0 - (value - 5.0) * 10.0
    return _factor("ipc", WEIGHTS["ipc"], score, "meta.ipc_p99_ms",
                   f"P99 {value}ms")


def _errors(agent: Agent, log_root: str | Path, stamp: int) -> dict[str, Any]:
    count = logs.recent_errors(log_root, agent.id, stamp)
    if count is None:
        return _unknown("errors", WEIGHTS["errors"], "log.recent_60s")
    score = 100.0 if count <= 10 else 100.0 - (count - 10) * 5.0
    return _factor("errors", WEIGHTS["errors"], score, "log.recent_60s",
                   f"最近 60 秒 {count} 条")


def _token(agent: Agent) -> dict[str, Any]:
    used = agent.used_pct()
    score = 100.0 if used < 80.0 else 100.0 - (used - 80.0) * 5.0
    return _factor("token", WEIGHTS["token"], score, "meta.token",
                   f"已用 {used:.1f}%")


def grade_of(score: int) -> str:
    if score >= 80:
        return "HEALTHY"
    if score >= 50:
        return "DEGRADED"
    if score >= 1:
        return "CRITICAL"
    return "DEAD"


def evaluate(agent: Agent, log_root: str | Path,
             stamp: int) -> dict[str, Any]:
    proc = read_proc(agent.pid, stamp)
    rows = [
        _heartbeat(agent),
        _cpu(agent, proc),
        _memory(agent, proc),
        _ipc(agent),
        _errors(agent, log_root, stamp),
        _token(agent),
    ]
    total = sum(WEIGHTS.values())
    known = sum(row["weight"] for row in rows if row["known"])
    coverage = known * 100.0 / total
    score = None
    grade = "UNKNOWN"
    if known:
        raw = sum(row["weight"] * row["score"]
                  for row in rows if row["known"]) / known
        score = int(round(raw))
        if coverage >= MIN_COVER:
            grade = grade_of(score)
    return {
        "agent": agent.id,
        "state": agent.state.value,
        "score": score,
        "grade": grade,
        "coverage_pct": round(coverage, 1),
        "renormalized": coverage < 100.0,
        "factors": rows,
        "unknown": [row["name"] for row in rows if not row["known"]],
    }
