# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 告警守护进程
#
# 从 /run/agentrt/alert.sock 接收换行分隔的 JSON 记录，交给引擎处理，把结论
# 以 JSON 行写到标准输出（systemd 收进 journal），并维护活动告警、归档与
# 状态文件。守护进程只读时钟、不读业务配置：规则目录、套接字与状态根全部
# 由命令行给出，单元文件负责装机后的默认值。

"""Unix socket alert daemon."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from .engine import Engine
from .model import NS, AlertEvent, BadEvent
from .rules import RuleError, load_rules
from .store import Store

DEFAULT_SOCKET = "/run/agentrt/alert.sock"
DEFAULT_STATE = "/var/lib/agentrt/alerts"
DEFAULT_RULES = (
    "/usr/share/airymax/alert/rules.d",
    "/etc/agentrt/alert/rules.d",
)
SOCK_MODE = 0o660
MAX_LINE = 65536
TICK_NS = NS
WATCH_NS = 10 * NS
SAVE_NS = NS // 2
# 只有状态迁移进归档：NOTIFY 只是同一告警的重复计数，留在 active.json
# 与 journal 里即可，写入归档会让持久化随重复频率线性增长。
ARCHIVE = frozenset({"EMIT", "REFIRE", "ESCALATE", "PENDING", "RESOLVE", "ACK"})
PUSH = frozenset({"EMIT", "ESCALATE"})


def sd_notify(state: str) -> None:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(state.encode("utf-8"))
    except OSError:
        return


class Server:
    """Socket front end around the deterministic engine."""

    def __init__(self, sock_path: str, rules: Iterable[str], state: str):
        self.sock_path = Path(sock_path)
        self.rules_paths = list(rules)
        self.store = Store(state)
        self.engine = Engine(load_rules(self.rules_paths))
        self.sel = selectors.DefaultSelector()
        self.conns: set[socket.socket] = set()
        self.bufs: dict[socket.socket, bytes] = {}
        self.srv: socket.socket | None = None
        self.stop = False
        self.want_reload = False
        self.served = 0
        self.once = False
        self.last_save = -NS

    def request_stop(self) -> None:
        self.stop = True

    def request_reload(self) -> None:
        self.want_reload = True

    def setup(self) -> None:
        self.sock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.sock_path.unlink()
        except FileNotFoundError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(self.sock_path))
        os.chmod(self.sock_path, SOCK_MODE)
        srv.listen(16)
        srv.setblocking(False)
        self.srv = srv
        self.sel.register(srv, selectors.EVENT_READ, None)
        sd_notify("READY=1\nSTATUS=alert daemon ready")

    def run(self, once: bool = False) -> int:
        self.once = once
        last_tick = time.monotonic_ns()
        last_watch = last_tick
        try:
            self.setup()
            self._save(last_tick, force=True)
            print(json.dumps({
                "action": "READY",
                "socket": str(self.sock_path),
                "rules": len(self.engine.rules),
            }), flush=True)
            while not self.stop:
                for key, _mask in self.sel.select(0.5):
                    if key.data is None:
                        self._accept()
                    else:
                        self._read(key.data)
                now = time.monotonic_ns()
                if now - last_tick >= TICK_NS:
                    last_tick = now
                    self._tick(now)
                if now - last_watch >= WATCH_NS:
                    last_watch = now
                    sd_notify("WATCHDOG=1")
                if self.want_reload:
                    self.want_reload = False
                    self._reload()
                if once and self.served and not self.conns:
                    break
        finally:
            self._cleanup()
        return 0

    def _accept(self) -> None:
        if self.srv is None:
            return
        conn, _peer = self.srv.accept()
        conn.setblocking(False)
        self.conns.add(conn)
        self.sel.register(conn, selectors.EVENT_READ, conn)
        self.served += 1

    def _close(self, conn: socket.socket) -> None:
        try:
            self.sel.unregister(conn)
        except (KeyError, ValueError):
            pass
        self.conns.discard(conn)
        self.bufs.pop(conn, None)
        try:
            conn.close()
        except OSError:
            pass

    def _read(self, conn: socket.socket) -> None:
        try:
            data = conn.recv(MAX_LINE)
        except BlockingIOError:
            return
        except OSError:
            self._close(conn)
            return
        if not data:
            self._close(conn)
            return
        buf = self.bufs.get(conn, b"") + data
        if len(buf) > MAX_LINE:
            self._reply(conn, {"error": "line too long"})
            self._close(conn)
            return
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if line.strip():
                self._handle(conn, line)
        self.bufs[conn] = buf

    def _handle(self, conn: socket.socket, line: bytes) -> None:
        now = time.monotonic_ns()
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            self._reply(conn, {"error": "malformed json"})
            return
        if not isinstance(msg, dict):
            self._reply(conn, {"error": "record must be an object"})
            return
        kind = msg.get("kind", "alert")
        try:
            acts = self._dispatch(kind, msg, now)
        except (BadEvent, ValueError) as exc:
            self._reply(conn, {"error": str(exc)})
            return
        if kind == "status":
            self._reply(conn, {"status": self.engine.status(now)})
            return
        if acts is None:
            return
        self._emit(acts, now)
        self._reply(conn, {"action": acts[-1].kind if acts else "OK"})

    def _dispatch(self, kind: str, msg: dict[str, Any], now: int):
        if kind == "alert":
            return self.engine.ingest(AlertEvent.build(msg, time.time_ns()), now)
        if kind == "recover":
            return self.engine.recover(AlertEvent.build(msg, time.time_ns()), now)
        if kind == "ack":
            return self.engine.ack(
                str(msg.get("alert_id", "")),
                str(msg.get("operator", "")),
                str(msg.get("comment", "")),
                now,
            )
        if kind == "status":
            return None
        if kind == "reload":
            self.want_reload = True
            return []
        raise BadEvent(f"unknown kind: {kind}")

    def _reply(self, conn: socket.socket, obj: Any) -> None:
        try:
            conn.sendall(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n")
        except OSError:
            self._close(conn)

    def _emit(self, acts: list[Any], now: int) -> None:
        dirty = False
        for act in acts:
            print(json.dumps(act.to_json(), ensure_ascii=False, separators=(",", ":")),
                  flush=True)
            if act.alert is not None and act.kind in ARCHIVE:
                self.store.append(act.alert)
                dirty = True
            if act.kind in PUSH and act.alert is not None:
                self._push(act)
        if dirty:
            self.store.save(self.engine.active.values())
        self._save(now, force=dirty)

    def _push(self, act: Any) -> None:
        sev = act.alert.event.severity.label
        if sev == "INFO":
            return
        text = act.alert.event.message.replace("\n", " ")[:80]
        sd_notify(f"STATUS={sev}: {text}")

    def _tick(self, now: int) -> None:
        acts = self.engine.tick(now)
        if acts:
            self._emit(acts, now)
        self._save(now)

    def _save(self, now: int, force: bool = False) -> None:
        if not force and now - self.last_save < SAVE_NS:
            return
        self.last_save = now
        self.store.save_status(self.engine.status(now))

    def _reload(self) -> None:
        rules = load_rules(self.rules_paths)
        self.engine.reload(rules)
        print(json.dumps({"action": "RELOAD", "rules": len(rules)}), flush=True)
        self._save(time.monotonic_ns(), force=True)

    def _cleanup(self) -> None:
        for conn in list(self.conns):
            self._close(conn)
        if self.srv is not None:
            try:
                self.sel.unregister(self.srv)
            except (KeyError, ValueError):
                pass
            self.srv.close()
            self.srv = None
        try:
            self.sock_path.unlink()
        except FileNotFoundError:
            pass
        self._save(time.monotonic_ns(), force=True)
        sd_notify("STOPPING=1")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="airy-alertd", description="AirymaxOS alert daemon")
    parser.add_argument("--socket", default=DEFAULT_SOCKET)
    parser.add_argument("--rules", action="append", default=None)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        server = Server(args.socket, args.rules or DEFAULT_RULES, args.state)
    except (RuleError, OSError, ValueError) as exc:
        print(json.dumps({"action": "FATAL", "error": str(exc)}), file=sys.stderr,
              flush=True)
        return 3

    signal.signal(signal.SIGTERM, lambda *_: server.request_stop())
    signal.signal(signal.SIGINT, lambda *_: server.request_stop())
    signal.signal(signal.SIGHUP, lambda *_: server.request_reload())

    try:
        return server.run(once=args.once)
    except (OSError, RuleError, ValueError) as exc:
        print(json.dumps({"action": "FATAL", "error": str(exc)}), file=sys.stderr,
              flush=True)
        return 3


if __name__ == "__main__":
    sys.exit(main())
