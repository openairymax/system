# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""airy-station 命令实现。

本层只做参数归一、权限放行前的审计、输出格式与退出码映射；采集在
facts/panel，渲染在 render，交互在 tui。Agent 启停与记忆擦除复用既有
运维包的公开处理函数，不复制它们的判定与落盘逻辑。
"""

import argparse
import io
import json
import os
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from agent import cli as agent_cli
from memory import config as mem_config
from memory import erase as mem_erase

from . import audit, config, facts, panel, parser, perm, render, tui
from .model import (
    BadInput,
    BadPerm,
    BadState,
    BadStation,
    BadUnsupported,
    PANELS,
    Panel,
    iso,
    need,
    rank_of,
)

CLI = "airy-station"
CODE = {2: BadInput, 3: BadState, 4: BadPerm}
AGENT_CMD = {
    "start": agent_cli.cmd_start,
    "pause": agent_cli.cmd_pause,
    "resume": agent_cli.cmd_resume,
    "stop": agent_cli.cmd_stop,
    "restart": agent_cli.cmd_restart,
}


@dataclass
class Ctx:
    base: Path
    cfg: Dict[str, Any]
    level: str
    args: argparse.Namespace


def _dump(doc: Any) -> None:
    print(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True))


def _raise(exc: Exception) -> None:
    """把既有包的异常码翻译成本包的异常类型。"""
    code = int(getattr(exc, "code", 3) or 3)
    raise CODE.get(code, BadState)(str(exc)) from exc


def _show(sec, name: str, args: argparse.Namespace) -> None:
    if args.json:
        _dump({"panel": name, "status": sec.status,
               "reason": sec.reason, "rows": sec.rows})
    else:
        print(render.lines(sec, name))


def _reject(args: argparse.Namespace) -> None:
    """显式拒绝 v1.1.0 的远程与 Web 选项。"""
    seen = []
    if getattr(args, "web", False):
        seen.append("--web")
    if getattr(args, "node", None) is not None:
        seen.append("--node")
    if getattr(args, "rpc_port", None) is not None:
        seen.append("--rpc-port")
    if getattr(args, "port", None) is not None:
        seen.append("--port")
    if seen:
        raise BadUnsupported(
            "远程节点与 Web UI 属 v1.1.0，本版不提供: " + ", ".join(seen)
        )


def _logdir(args: argparse.Namespace) -> None:
    """把审计目录同时交给记忆擦除的审计，避免写默认系统路径。"""
    if not args.log_dir:
        return
    os.environ[audit.ENV] = str(args.log_dir)
    os.environ["AIRY_MEMORY_LOGDIR"] = str(args.log_dir)


def _limits(args: argparse.Namespace, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """按子命令的行数覆盖采集上限。"""
    out = dict(cfg)
    lines = int(getattr(args, "lines", 0) or 0)
    if lines > 0 and args.command == "logs":
        out["devstation.log_buffer_lines"] = lines
    if lines > 0 and args.command == "alerts":
        out["devstation.alert_buffer_size"] = lines
    return out


def cmd_panel(ctx: Ctx) -> int:
    """单面板视图；monitor 在真终端下进入交互界面。"""
    name = ctx.args.command
    cfg = _limits(ctx.args, ctx.cfg)
    snap = panel.build_one(ctx.base, name, ctx.level, cfg)
    sec = snap.section(name)
    if name == "monitor" and not ctx.args.json and not ctx.args.once \
            and sys.stdin.isatty() and sys.stdout.isatty():
        return tui.run(ctx.base, ctx.level, ctx.cfg, start=Panel.MONITOR)
    if name == "monitor" and not ctx.args.json:
        print(render.frame(snap, Panel.MONITOR, 100, 30))
    else:
        _show(sec, name, ctx.args)
    return 0


def cmd_snapshot(ctx: Ctx) -> int:
    """完整快照：--json 输出全部八节，文本只输出节摘要。"""
    snap = panel.build_snap(ctx.base, ctx.level, ctx.cfg)
    if ctx.args.json:
        _dump(snap.to_json())
        return 0
    print("stamp=%s node=%s level=%s" % (
        iso(snap.stamp), snap.node, snap.level))
    for item in PANELS:
        sec = snap.section(item.value)
        if sec.ready:
            print("%s rows=%d" % (item.value, len(sec.rows)))
        else:
            print("%s unavailable reason=%s" % (item.value, sec.reason))
    return 0


def _agent_op(ctx: Ctx) -> int:
    args = ctx.args
    need(bool(args.id), "--action 需要 --id", BadInput)
    need(args.action in AGENT_CMD, "未知 Agent 动作: %s" % args.action,
         BadInput)
    action = "agent." + args.action
    perm.authorize(ctx.level, action, args.yes, str(args.id))
    store, _ = facts.agent_store(ctx.base)
    opts = argparse.Namespace(id=str(args.id), json=False)
    try:
        with redirect_stdout(io.StringIO()) as buffered:
            code = int(AGENT_CMD[args.action](opts, store) or 0)
    except BadStation:
        raise
    except Exception as exc:  # 既有包的异常码在这里统一翻译
        _raise(exc)
    text = buffered.getvalue().strip()
    audit.record(ctx.level, action, str(args.id),
                 "ok" if code == 0 else "error", text)
    print(text or "%s rc=%d" % (args.id, code))
    return code


def cmd_agents(ctx: Ctx) -> int:
    """Agent 列表；带 --action 时执行受控操作。"""
    if ctx.args.action:
        return _agent_op(ctx)
    snap = panel.build_one(ctx.base, "agents", ctx.level, ctx.cfg)
    _show(snap.section("agents"), "agents", ctx.args)
    return 0


def _mem_erase(ctx: Ctx) -> int:
    args = ctx.args
    need(bool(args.agent), "erase 需要 --agent", BadInput)
    need(bool(args.items) ^ bool(args.all_items),
         "erase 需要 --items 或 --all（二选一）", BadInput)
    perm.authorize(ctx.level, "memory.erase", args.yes, str(args.agent))
    mcfg = mem_config.load()
    store, _ = facts.memory_store(ctx.base, int(mcfg["l3_block_kb"]))
    opts = {"agent": str(args.agent), "items": args.items,
            "all": args.all_items, "level": args.tier,
            "method": args.method}
    try:
        row = mem_erase.erase(store, mcfg, opts)
    except BadStation:
        raise
    except Exception as exc:  # 既有包的异常码在这里统一翻译
        _raise(exc)
    audit.record(ctx.level, "memory.erase", str(args.agent), "ok",
                 json.dumps(row, ensure_ascii=False, sort_keys=True))
    if args.json:
        _dump(row)
    else:
        print("erase agent=%s level=%s method=%s passes=%s bytes=%s" % (
            row.get("agent"), row.get("level"), row.get("method"),
            row.get("passes"), row.get("bytes")))
    return 0


def cmd_memory(ctx: Ctx) -> int:
    """记忆视图；--action erase 走 admin 通道。"""
    if ctx.args.action == "erase":
        return _mem_erase(ctx)
    snap = panel.build_one(ctx.base, "memory", ctx.level, ctx.cfg)
    _show(snap.section("memory"), "memory", ctx.args)
    return 0


HANDLERS = {
    "agents": cmd_agents,
    "memory": cmd_memory,
    "snapshot": cmd_snapshot,
}


def main(argv=None) -> int:
    """解析参数并分派；可预期失败映射为 2/3/4。"""
    args = parser.build().parse_args(argv)
    try:
        _reject(args)
        cfg = config.load(args.conf)
        if cfg["devstation.web_enabled"]:
            raise BadUnsupported(
                "Web UI 属 v1.1.0；devstation.web_enabled=true 已显式拒绝"
            )
        level = args.level or cfg["devstation.permission_level"]
        rank_of(level)
        _logdir(args)
        base = Path(str(args.root or "/"))
        ctx = Ctx(base=base, cfg=cfg, level=level, args=args)
        if args.command is None:
            return tui.run(base, level, cfg, once=args.once)
        handler = HANDLERS.get(args.command, cmd_panel)
        return int(handler(ctx) or 0)
    except BadStation as exc:
        print("%s: %s" % (CLI, exc), file=sys.stderr)
        return int(getattr(exc, "code", 3))
    except OSError as exc:
        print("%s: %s" % (CLI, exc), file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("%s: 已中断" % CLI, file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
