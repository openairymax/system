# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS Agent 资源调优与令牌预算
#
# 调优先把候选值算完再整体校验（runtime <= deadline <= period、
# l1 <= l2），校验失败时 Agent 不被改动；--auto 把调度与内存恢复到
# /etc/agentrt 默认值并打开自动调优标记。令牌预算变更同样落到 meta.json。

"""Scheduling, memory and token budget mutations."""

from __future__ import annotations

from typing import Any

from airylib.err import need

from .model import (BadInput, BadState, Agent, State, iso, need_mem,
                    need_sched, now_ns)

_SCHED = ("runtime_ms", "deadline_ms", "period_ms")
_MEM = ("l1_mb", "l2_mb")
_LOCKED = (State.DEAD, State.STOPPING)


def apply(agent: Agent, cfg: Any, opts: dict[str, Any],
          stamp: int = 0) -> None:
    need(agent.state not in _LOCKED,
         f"Agent {agent.id}: {agent.state.value} 不允许调优", BadState)
    auto = bool(opts.get("auto"))
    explicit = [key for key in _SCHED + _MEM if opts.get(key) is not None]
    need(not (auto and explicit), "--auto 不能与显式参数同时使用", BadInput)
    need(auto or explicit, "tune 至少需要一个参数或 --auto", BadInput)
    sched = dict(agent.sched)
    memory = dict(agent.memory)
    if auto:
        sched = {
            "runtime_ms": cfg["default_sched_runtime_ms"],
            "deadline_ms": cfg["default_sched_deadline_ms"],
            "period_ms": cfg["default_sched_period_ms"],
        }
        memory = {
            "l1_mb": cfg["default_l1_mb"],
            "l2_mb": cfg["default_l2_mb"],
        }
    for key in _SCHED:
        if opts.get(key) is not None:
            sched[key] = opts[key]
    for key in _MEM:
        if opts.get(key) is not None:
            memory[key] = opts[key]
    need_sched(sched)
    need_mem(memory)
    agent.sched = sched
    agent.memory = memory
    agent.auto_tune = auto
    at = stamp or now_ns()
    agent.audit("auto-tune" if auto else "tune", at)


def set_budget(agent: Agent, amount: int, stamp: int = 0) -> None:
    need(isinstance(amount, int) and not isinstance(amount, bool)
         and amount > 0, "令牌总额必须是正整数", BadInput)
    agent.token["total_budget"] = amount
    agent.audit("token-budget", stamp or now_ns())


def reset_tokens(agent: Agent, stamp: int = 0) -> None:
    at = stamp or now_ns()
    agent.token["consumed"] = 0
    agent.token["last_reset_at"] = iso(at)
    agent.audit("token-reset", at)


def token_status(agent: Agent, stamp: int = 0) -> dict[str, Any]:
    at = stamp or now_ns()
    return {
        "agent": agent.id,
        "total_budget": agent.token["total_budget"],
        "consumed": agent.token["consumed"],
        "remaining": agent.remaining(),
        "used_pct": agent.used_pct(),
        "rate_per_sec": agent.rate(at),
        "reset_period": agent.token.get("reset_period", "manual"),
        "last_reset_at": agent.token.get("last_reset_at") or None,
        "rate_source": "average-since-reset",
    }
