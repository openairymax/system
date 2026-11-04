# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 固件加载实况
#
# 期望来自模块自报：modinfo -F firmware 给出的是驱动源码里 MODULE_FIRMWARE
# 写下的文件名，不随发行版漂移。实况来自本次开机留下的内核日志：固件加载器
# 每次取件写一行，成功写 direct-loading，失败写 failed to load 或
# Direct firmware load for ... failed。
#
# 两侧都只取既有事实，不做版本推断。三个状态含义明确：
#   loaded   日志证明这个名字被成功载入；
#   missing  日志证明这个名字载入失败；
#   unknown  没有证据——模块已载入但没请求过它，或环形缓冲已被覆盖。
# unknown 不等于 loaded：把没有证据读成「没问题」，正是本命令要避免的错误。
#
# 口径限制：只清点 /proc/modules 里已载入的模块，内建驱动不出现；日志按
# 文件名比对，固件加载器报点用的就是文件名。
#
# 另有三类与具体文件名无关的失败签名（无匹配固件、微码初始化失败、驱动与
# 固件 API 档位不一致），单独成列，因为它们不会落在任何一个期望文件名上。

"""AirymaxOS firmware load survey (FW-03)."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from posixpath import basename

__all__ = [
    "LOADED", "MISSING", "UNKNOWN", "Item", "Survey", "collect",
    "expected", "kernel_log", "load_list",
]

LOADED = "loaded"

MISSING = "missing"

UNKNOWN = "unknown"

MODULES = "/proc/modules"

_PASS = (re.compile(r"direct-loading firmware\s+(\S+)"),)

_FAIL = (re.compile(r"failed to load\s+(\S+)"),
         re.compile(r"Direct firmware load for\s+(\S+)\s+failed"))

_HINTS = (re.compile(r"no suitable firmware found", re.I),
          re.compile(r"Failed to run INIT ucode", re.I),
          re.compile(r"api flags index \d+ larger", re.I))

_STAMP = re.compile(r"^\[\s*[0-9.]+\]\s*")


@dataclass(frozen=True, slots=True)
class Item:
    module: str
    file: str
    state: str


@dataclass(frozen=True, slots=True)
class Survey:
    items: tuple[Item, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def modules(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.module for item in self.items))

    def items_of(self, module: str) -> tuple[Item, ...]:
        return tuple(item for item in self.items if item.module == module)

    def count(self, state: str) -> int:
        return sum(1 for item in self.items if item.state == state)

    def names(self, state: str) -> tuple[str, ...]:
        return tuple(f"{item.module}:{item.file}" for item in self.items
                     if item.state == state)

    def dump(self) -> dict[str, object]:
        return {
            "modules": [
                {"module": mod,
                 "files": [{"file": item.file, "state": item.state}
                           for item in self.items_of(mod)]}
                for mod in self.modules
            ],
            "files": len(self.items),
            "loaded": self.count(LOADED),
            "missing": list(self.names(MISSING)),
            "unknown": list(self.names(UNKNOWN)),
            "warnings": list(self.warnings),
        }


def collect(mods: Iterable[str], log: str,
            expect: Callable[[str], Sequence[str]] = None) -> Survey:
    """把「已载入模块」与「本次开机日志」对成一份固件报告。

    模块顺序即载入顺序（/proc/modules 本就是那个顺序），文件名顺序沿用
    modinfo 的输出，两者都不再排序：现场报告要能按「谁先起来的」读。
    """
    lookup = expect or expected
    have = {_name(raw) for raw in _hits(_PASS, log)}
    lost = {_name(raw) for raw in _hits(_FAIL, log)}
    items = [Item(mod, name, _state(_name(name), have, lost))
             for mod in mods for name in lookup(mod)]
    return Survey(tuple(items), _warns(log))


def expected(module: str) -> tuple[str, ...]:
    """模块自报的固件文件名；模块缺失或 modinfo 不可用时为空。"""
    try:
        res = subprocess.run(["modinfo", "-F", "firmware", module],
                             capture_output=True, text=True, check=False)
    except OSError:
        return ()
    if res.returncode != 0:
        return ()
    return tuple(line.strip() for line in res.stdout.splitlines()
                 if line.strip())


def load_list(path: str | Path = MODULES) -> tuple[str, ...]:
    """已载入模块名：/proc/modules 首列。读不到时为空而不是报错。"""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    return tuple(line.split()[0] for line in text.splitlines()
                 if line.split())


def kernel_log(path: str | None = None) -> str:
    """内核日志全文。取不到时为空：调用方据此得到 unknown，而非 loaded。"""
    if path:
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    try:
        res = subprocess.run(["dmesg"], capture_output=True, text=True,
                             check=False)
    except OSError:
        return ""
    return res.stdout


def _hits(pats: Sequence[re.Pattern], log: str) -> list[str]:
    found: list[str] = []
    for pat in pats:
        found.extend(pat.findall(log))
    return found


def _name(raw: str) -> str:
    return basename(raw.strip().rstrip(",;:"))


def _state(want: str, have: set[str], lost: set[str]) -> str:
    if want in lost:
        return MISSING
    return LOADED if want in have else UNKNOWN


def _warns(log: str) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for line in log.splitlines():
        text = line.strip()
        key = _STAMP.sub("", text)
        if text and key not in seen and any(p.search(text) for p in _HINTS):
            seen.add(key)
            out.append(text)
    return tuple(out)
