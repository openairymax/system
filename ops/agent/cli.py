# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS Agent 运维命令行
#
# 子命令对应设计 §9 的 airyctl agent 子树：create/start/pause/resume/stop/
# terminate/restart/status/list/tune/token/log，外加 §3 的 health 评估。
# 退出码：0 成功、2 参数错误、3 状态或存储不允许、4 airy-log 权限拒绝。
# 跨节点迁移与滚动升级（§9.3）本版不实现，migrate/upgrade 只返回固定错误。

"""Agent operations command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import config, health, logs, tune
from .model import (BadAgent, BadConfig, BadInput, BadPerm, BadState,
                    BadStore, BadUnsupported, State, check_id, iso,
                    now_ns, parse_mb, parse_ms, split_caps)
from .parser import build
from .store import Store

OK = 0
ERR_INPUT = 2
ERR_STATE = 3
ERR_PERM = 4


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _cfg(args: argparse.Namespace) -> config.Config:
    return config.load(getattr(args, "conf", None))


def _load(store: Store, args: argparse.Namespace) -> Any:
    return store.load(check_id(args.id))


def _spawn(agent: Any, action: str, stamp: int) -> None:
    for target in (State.SPAWNING, State.READY, State.RUNNING):
        agent.switch(target, action, stamp)


def _halt(agent: Any, action: str, stamp: int) -> None:
    if agent.state is not State.STOPPING:
        agent.switch(State.STOPPING, action, stamp)
    agent.switch(State.STOPPED, action, stamp)


def _emit(args: argparse.Namespace, agent: Any, text: str) -> int:
    print(_dump(agent.to_json()) if getattr(args, "json", False) else text)
    return OK


def _detail(agent: Any) -> str:
    free = agent.remaining()
    caps = ",".join(agent.capabilities) or "-"
    return "\n".join([
        f"{agent.id} {agent.name} {agent.state.value}",
        f"template={agent.template} created={iso(agent.created_at)}"
        f" updated={iso(agent.updated_at)}",
        f"token consumed={agent.token['consumed']}"
        f" total={agent.token['total_budget']} remaining={free}"
        f" used_pct={agent.used_pct()}",
        f"sched runtime={agent.sched['runtime_ms']}ms"
        f" deadline={agent.sched['deadline_ms']}ms"
        f" period={agent.sched['period_ms']}ms auto_tune={agent.auto_tune}",
        f"memory l1={agent.memory['l1_mb']}MB l2={agent.memory['l2_mb']}MB"
        f" pid={agent.pid}",
        f"capabilities={caps}",
    ])


def _mem_opts(opts: dict[str, Any], args: argparse.Namespace) -> None:
    if args.memory_l1 is not None:
        opts["l1_mb"] = parse_mb(args.memory_l1)
    if args.memory_l2 is not None:
        opts["l2_mb"] = parse_mb(args.memory_l2)


def _sched_opts(opts: dict[str, Any], args: argparse.Namespace) -> None:
    if args.sched_runtime is not None:
        opts["runtime_ms"] = parse_ms(args.sched_runtime)
    if args.sched_deadline is not None:
        opts["deadline_ms"] = parse_ms(args.sched_deadline)
    if args.sched_period is not None:
        opts["period_ms"] = parse_ms(args.sched_period)


def cmd_create(args: argparse.Namespace, store: Store) -> int:
    cfg = _cfg(args)
    opts: dict[str, Any] = {
        "name": args.name,
        "template": args.template,
        "capabilities": split_caps(args.capabilities)
        if args.capabilities is not None else [],
    }
    if args.token_budget is not None:
        opts["token_budget"] = args.token_budget
    _mem_opts(opts, args)
    _sched_opts(opts, args)
    agent = store.create(cfg, opts)
    return _emit(args, agent, f"{agent.id} {agent.name} {agent.state.value}")


def cmd_start(args: argparse.Namespace, store: Store) -> int:
    agent = _load(store, args)
    stamp = now_ns()
    _spawn(agent, "start", stamp)
    store.save(agent)
    return _emit(args, agent, f"{agent.id} {agent.state.value}")


def cmd_pause(args: argparse.Namespace, store: Store) -> int:
    agent = _load(store, args)
    agent.switch(State.BLOCKED, "pause", now_ns())
    store.save(agent)
    return _emit(args, agent, f"{agent.id} {agent.state.value}")


def cmd_resume(args: argparse.Namespace, store: Store) -> int:
    agent = _load(store, args)
    stamp = now_ns()
    if agent.state is State.STOPPED:
        agent.switch(State.READY, "resume", stamp)
    agent.switch(State.RUNNING, "resume", stamp)
    store.save(agent)
    return _emit(args, agent, f"{agent.id} {agent.state.value}")


def cmd_stop(args: argparse.Namespace, store: Store) -> int:
    agent = _load(store, args)
    _halt(agent, "stop", now_ns())
    store.save(agent)
    return _emit(args, agent, f"{agent.id} {agent.state.value}")


def cmd_restart(args: argparse.Namespace, store: Store) -> int:
    agent = _load(store, args)
    stamp = now_ns()
    if agent.state is not State.INACTIVE and agent.state is not State.STOPPED:
        _halt(agent, "restart", stamp)
    _spawn(agent, "restart", stamp)
    store.save(agent)
    return _emit(args, agent, f"{agent.id} {agent.state.value}")


def cmd_terminate(args: argparse.Namespace, store: Store) -> int:
    agent = _load(store, args)
    stamp = now_ns()
    if agent.state in (State.INACTIVE, State.STOPPED):
        agent.switch(State.DEAD, "terminate", stamp)
    else:
        _halt(agent, "terminate", stamp)
        agent.switch(State.DEAD, "terminate", stamp)
    store.save(agent)
    path = store.archive_agent(agent, stamp)
    return _emit(args, agent,
                 f"{agent.id} {agent.state.value} archived={path.name}")


def cmd_status(args: argparse.Namespace, store: Store) -> int:
    agent = _load(store, args)
    return _emit(args, agent, _detail(agent))


def _norm_state(text: str | None) -> str | None:
    return (text or "").strip().upper() or None


def _want_state(args: argparse.Namespace) -> Any:
    state = _norm_state(args.state)
    if state is not None and state not in State.__members__:
        raise BadInput(f"未知状态: {args.state}")
    return state


def _list_rows(rows: Any) -> None:
    for item in rows:
        print(f"{item.id}\t{item.name}\t{item.state.value}\t"
              f"{item.token['consumed']}/{item.token['total_budget']}\t"
              f"{item.template}")


def cmd_list(args: argparse.Namespace, store: Store) -> int:
    state = _want_state(args)
    rows = store.rows(state)
    if args.json:
        print(_dump([item.to_json() for item in rows]))
        return OK
    _list_rows(rows)
    return OK


def cmd_tune(args: argparse.Namespace, store: Store) -> int:
    opts = {
        "auto": args.auto,
        "runtime_ms": parse_ms(args.sched_runtime)
        if args.sched_runtime is not None else None,
        "deadline_ms": parse_ms(args.sched_deadline)
        if args.sched_deadline is not None else None,
        "period_ms": parse_ms(args.sched_period)
        if args.sched_period is not None else None,
        "l1_mb": parse_mb(args.memory_l1)
        if args.memory_l1 is not None else None,
        "l2_mb": parse_mb(args.memory_l2)
        if args.memory_l2 is not None else None,
    }
    agent = _load(store, args)
    tune.apply(agent, _cfg(args), opts, now_ns())
    store.save(agent)
    text = (f"{agent.id} auto={agent.auto_tune}"
            f" runtime={agent.sched['runtime_ms']}ms"
            f" l1={agent.memory['l1_mb']}MB l2={agent.memory['l2_mb']}MB")
    return _emit(args, agent, text)


def cmd_token(args: argparse.Namespace, store: Store) -> int:
    agent = _load(store, args)
    stamp = now_ns()
    if args.action == "budget":
        tune.set_budget(agent, args.amount, stamp)
        store.save(agent)
        return _emit(args, agent,
                     f"{agent.id} budget={agent.token['total_budget']}")
    if args.action == "reset":
        tune.reset_tokens(agent, stamp)
        store.save(agent)
        return _emit(args, agent,
                     f"{agent.id} consumed=0 remaining={agent.remaining()}")
    doc = tune.token_status(agent, stamp)
    if args.json:
        print(_dump(doc))
        return OK
    print(f"{agent.id} total={doc['total_budget']}"
          f" consumed={doc['consumed']} remaining={doc['remaining']}"
          f" used_pct={doc['used_pct']} rate_per_sec={doc['rate_per_sec']}"
          f" reset_period={doc['reset_period']}"
          f" last_reset={doc['last_reset_at']}")
    return OK


def _log_root(args: argparse.Namespace) -> str:
    return (args.log_root or os.environ.get("AIRY_AGENT_LOG_ROOT")
            or logs.LOG_ROOT)


def _log_arc(args: argparse.Namespace) -> str:
    return (args.log_archive or os.environ.get("AIRY_AGENT_LOG_ARCHIVE")
            or logs.LOG_ARCHIVE)


def _arch_done(args: argparse.Namespace, raw: str) -> int:
    ident = check_id(raw)
    moved = logs.archive_logs(_log_root(args), _log_arc(args), ident,
                              _cfg(args)["log_retention_d"], now_ns())
    print(f"{ident} archived={len(moved)}"
          + (f" files={' '.join(moved)}" if moved else ""))
    return OK


def _log_archive(args: argparse.Namespace) -> Any:
    items = args.items
    if not (items and items[0] == "archive"):
        return None
    if len(items) != 2:
        raise BadInput("用法: log archive <id>")
    return _arch_done(args, items[1])


def _log_follow(args: argparse.Namespace, ident: str) -> None:
    try:
        logs.follow_logs(_log_root(args), ident, args.lines)
    except KeyboardInterrupt:
        print()


def _log_tail(args: argparse.Namespace, ident: str) -> int:
    if args.follow:
        _log_follow(args, ident)
        return OK
    rows = logs.tail_lines(_log_root(args), ident, args.lines)
    if not rows:
        print(f"无日志: {ident}")
        return OK
    for line in rows:
        print(line)
    return OK


def cmd_log(args: argparse.Namespace, store: Store) -> int:
    logs.ensure_access()
    done = _log_archive(args)
    if done is not None:
        return done
    items = args.items
    if len(items) != 1:
        raise BadInput("用法: log <id> [--lines N] [--follow]")
    ident = check_id(items[0])
    return _log_tail(args, ident)


def cmd_health(args: argparse.Namespace, store: Store) -> int:
    agent = _load(store, args)
    doc = health.evaluate(agent, _log_root(args), now_ns())
    if args.json:
        print(_dump(doc))
        return OK
    score = "n/a" if doc["score"] is None else doc["score"]
    print(f"{doc['agent']} {doc['state']} grade={doc['grade']}"
          f" score={score} coverage={doc['coverage_pct']}%"
          f" renormalized={doc['renormalized']}")
    for row in doc["factors"]:
        value = "unknown" if row["score"] is None else row["score"]
        print(f"  {row['name']} weight={row['weight']} score={value}"
              f" source={row['source']} {row['detail']}")
    if doc["unknown"]:
        print("  unknown=" + ",".join(doc["unknown"]))
    return OK


def cmd_unsupported(args: argparse.Namespace, store: Store) -> int:
    raise BadUnsupported("本版单节点不提供跨节点迁移与滚动升级")


HANDLERS = {
    "create": cmd_create,
    "start": cmd_start,
    "pause": cmd_pause,
    "resume": cmd_resume,
    "stop": cmd_stop,
    "terminate": cmd_terminate,
    "restart": cmd_restart,
    "status": cmd_status,
    "list": cmd_list,
    "tune": cmd_tune,
    "token": cmd_token,
    "log": cmd_log,
    "health": cmd_health,
    "migrate": cmd_unsupported,
    "upgrade": cmd_unsupported,
}


def main(argv: list[str] | None = None) -> int:
    args = build(HANDLERS).parse_args(argv)
    store = Store(args.root)
    try:
        return args.func(args, store)
    except BadPerm as exc:
        print(f"airy-agent: {exc}", file=sys.stderr)
        return ERR_PERM
    except BadInput as exc:
        print(f"airy-agent: {exc}", file=sys.stderr)
        return ERR_INPUT
    except (BadState, BadStore, BadConfig, BadUnsupported) as exc:
        print(f"airy-agent: {exc}", file=sys.stderr)
        return ERR_STATE
    except BadAgent as exc:
        print(f"airy-agent: {exc}", file=sys.stderr)
        return ERR_STATE
    except OSError as exc:
        print(f"airy-agent: {exc}", file=sys.stderr)
        return ERR_STATE


if __name__ == "__main__":
    sys.exit(main())
