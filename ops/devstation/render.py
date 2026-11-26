# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""确定性纯文本帧渲染。

同一份快照在给定宽高下逐字节稳定：不读时钟、不读终端、不随机。帧的
每一行不超过 width，总行数恰好等于 height，缺源面板显示 unavailable
与原因而不是空白。
"""

from .model import Panel, Snapshot, TITLE

COLS = {
    "monitor": ("daemon", "cpu_pct", "rss_bytes", "health_score",
                "restarts", "heartbeat_ms"),
    "logs": ("ts", "level", "source", "message"),
    "agents": ("id", "name", "state", "score", "grade", "pid"),
    "alerts": ("severity", "state", "source", "agent", "code", "message",
               "count"),
    "incidents": ("incident_id", "severity", "status", "title",
                  "detected_at"),
    "memory": ("agent", "l3_items", "l3_bytes", "l3_frag", "l4_items",
               "l4_bytes", "l4_frag"),
    "backup": ("backup_id", "kind", "created_at", "size_bytes",
               "total_files"),
    "config": ("file", "key", "value"),
}


def _fit(text, width: int) -> str:
    """压成单行并截断到宽度，防止换行把帧撑破。"""
    return " ".join(str(text).splitlines())[:max(int(width), 0)]


def _cell(value) -> str:
    return "-" if value is None else str(value)


def _row(row, cols) -> str:
    return "  ".join(_cell(row.get(key)) for key in cols)


def _flat(row) -> str:
    return " ".join(_cell(value) for value in row.values()).lower()


def _body(sec, name: str, needle: str, width: int, cap: int) -> list:
    """生成面板正文；cap 为最大行数，含列名行。"""
    if cap <= 0:
        return []
    if not sec.ready:
        return [_fit("[%s] unavailable: %s" % (sec.name, sec.reason),
                     width)]
    cols = COLS.get(name, ())
    rows = list(sec.rows)
    if needle:
        low = needle.lower()
        rows = [row for row in rows if low in _flat(row)]
    out = [_fit("  ".join(cols), width)]
    if rows:
        out.extend(_fit(_row(row, cols), width)
                   for row in rows[-(cap - 1):])
    else:
        out.append("(空)")
    return out[:cap]


def _foot(name: str, needle: str, width: int) -> str:
    text = ("%s | filter=%s | 1-8 切换 / 过滤 r 刷新 q 退出"
            % (name, needle or "-"))
    return _fit(text, width)


def frame(snap: Snapshot, name, width: int = 100,
          height: int = 30, needle: str = "") -> str:
    """渲染一帧：固定高度与宽度，缺失行用空行补齐。"""
    panel = Panel(name)
    w = max(int(width), 8)
    h = max(int(height), 3)
    head = _fit("AirymaxOS DevStation 0.0.5 node=%s panel=%s level=%s"
                % (snap.node, TITLE[panel], snap.level), w)
    cap = h - 3
    body = _body(snap.section(panel.value), panel.value, needle, w, cap)
    body.extend([""] * (cap - len(body)))
    out = [head, "-" * w] + body + [_foot(panel.value, needle, w)]
    if len(out) > h:
        out = out[:h]
    while len(out) < h:
        out.append("")
    return "\n".join(_fit(line, w) for line in out)


def lines(sec, name, width: int = 120) -> str:
    """单面板文本输出，不补行也不截高。"""
    panel = Panel(name)
    w = max(int(width), 8)
    head = _fit("%s status=%s%s" % (
        panel.value, sec.status,
        "" if sec.ready else " reason=" + sec.reason), w)
    body = _body(sec, panel.value, "", w, len(sec.rows) + 2)
    return "\n".join([head] + body)
