# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""curses 交互循环。

只有标准输入输出都是真实终端时才进入 curses；重定向、管道与 --once
一律退回 render.frame 的纯文本单帧，保证脚本与门禁可复现。交互键：
1-8 切换面板、r/空格刷新、/ 过滤、q 或 Esc 退出。
"""

import sys

from . import panel, render
from .model import HOTKEY, PANELS


def _size(stdscr) -> tuple:
    """取终端尺寸；过小按渲染下限补齐。"""
    height, width = stdscr.getmaxyx()
    return max(width, 40), max(height, 6)


def _draw(stdscr, snap, name: str, needle: str) -> None:
    width, height = _size(stdscr)
    text = render.frame(snap, name, width, height, needle)
    stdscr.erase()
    for line_no, line in enumerate(text.splitlines()[:height]):
        try:
            stdscr.addnstr(line_no, 0, line, width - 1)
        except Exception:
            break
    stdscr.refresh()


def _prompt(stdscr, label: str) -> str:
    import curses

    height, width = stdscr.getmaxyx()
    stdscr.timeout(-1)
    curses.echo()
    try:
        stdscr.addnstr(height - 1, 0, label + ": ", width - 1)
        raw = stdscr.getstr(height - 1, len(label) + 2, max(width - 8, 1))
    finally:
        curses.noecho()
    return raw.decode("utf-8", "replace").strip()


def _loop(stdscr, root, level, cfg, name: str, needle: str,
          interval: int) -> None:
    import curses

    curses.curs_set(0)
    stdscr.keypad(True)
    stdscr.timeout(max(int(interval), 1) * 1000)
    snap = panel.build_snap(root, level, cfg)
    current = name
    while True:
        _draw(stdscr, snap, current, needle)
        key = stdscr.getch()
        if key in (-1, curses.KEY_RESIZE):
            snap = panel.build_snap(root, level, cfg)
            continue
        if key in (ord("q"), ord("Q"), 27):
            return
        if key in (ord("r"), ord("R"), ord(" ")):
            snap = panel.build_snap(root, level, cfg)
            continue
        if key == ord("/"):
            needle = _prompt(stdscr, "过滤")
            snap = panel.build_snap(root, level, cfg)
            continue
        text = chr(key) if 0 <= key < 128 else ""
        if text in HOTKEY:
            current = HOTKEY[text].value


def run(root, level, cfg, once: bool = False, needle: str = "",
        start=None) -> int:
    """入口：真终端进 curses，否则打印一帧后返回。"""
    panel_name = (start or PANELS[0]).value
    snap = panel.build_snap(root, level, cfg)
    if once or not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(render.frame(snap, panel_name, 100, 30, needle))
        return 0
    import curses

    interval = int(cfg["devstation.refresh_interval_s"])
    curses.wrapper(_loop, root, level, cfg, panel_name, needle, interval)
    return 0
