# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# locale 矩阵与 locale.conf 的唯一解析入口
#
# 支持项只有一份数据（matrix.tsv），列出、生成、校验三条路径都从这里读，
# 不在命令行里另抄一份清单。判定与读取分开：load 只做语法，problems 只做
# 结构判定，调用方据此决定退出码，不必在解析中途抛不同异常。

"""AirymaxOS locale matrix."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT",
    "PRIORITIES",
    "WANT",
    "Row",
    "conf_problems",
    "load",
    "problems",
    "read_conf",
]

DATA = Path(__file__).with_name("matrix.tsv")
DEFAULT = "en_US.UTF-8"
PRIORITIES = ("P0", "P1", "P2")
WANT = 8
MAX_NAME = 32


@dataclass(frozen=True)
class Row:
    """矩阵中的一条 locale。"""

    posix: str
    lang: str
    terr: str
    codeset: str
    prio: str

    @property
    def source(self) -> str:
        """localedef -i 的输入名（语言_区域）。"""
        return f"{self.lang}_{self.terr}" if self.terr else self.lang


def load(path: Path | str | None = None) -> list[Row]:
    """读取矩阵；列数不对直接抛 ValueError。"""
    src = Path(path) if path else DATA
    rows: list[Row] = []
    for num, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split()
        if not fields or fields[0].startswith("#"):
            continue
        if len(fields) != 5:
            raise ValueError(f"矩阵第 {num} 行应为 5 列: {line!r}")
        rows.append(Row(*fields))
    return rows


def read_conf(path: Path | str | None = None) -> dict[str, str]:
    """读取 locale.conf 的 KEY=VALUE，去掉成对引号。"""
    src = Path(path or "/etc/locale.conf")
    out: dict[str, str] = {}
    for line in src.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, val = text.split("=", 1)
        out[key.strip()] = val.strip().strip("\"'")
    return out


def problems(rows: list[Row]) -> list[str]:
    """矩阵结构判定：条数、唯一性、优先级、字符集、默认项。"""
    bad: list[str] = []
    if len(rows) != WANT:
        bad.append(f"矩阵条目数 {len(rows)}，应为 {WANT}")
    seen: set[str] = set()
    for row in rows:
        bad.extend(_row_bad(row, seen))
        seen.add(row.posix)
    if DEFAULT not in seen:
        bad.append(f"缺少默认 locale {DEFAULT}")
    return bad


def conf_problems(env: dict[str, str], rows: list[Row]) -> list[str]:
    """已配置项必须是矩阵成员，且名称为 UTF-8。"""
    known = {row.posix for row in rows}
    bad: list[str] = []
    if not env.get("LANG"):
        bad.append("locale.conf 缺少 LANG")
    for key, val in env.items():
        if key != "LANG" and not key.startswith("LC_"):
            continue
        if val and val not in known:
            bad.append(f"{key}={val} 不在矩阵内")
    return bad


def _row_bad(row: Row, seen: set[str]) -> list[str]:
    bad: list[str] = []
    if row.prio not in PRIORITIES:
        bad.append(f"{row.posix}: 优先级 {row.prio} 不在 {PRIORITIES}")
    if row.codeset != "UTF-8":
        bad.append(f"{row.posix}: 字符集必须是 UTF-8")
    if row.posix in seen:
        bad.append(f"{row.posix}: 条目重复")
    if not row.posix.endswith("." + row.codeset):
        bad.append(f"{row.posix}: 名称与字符集不一致")
    if len(row.posix.encode()) > MAX_NAME:
        bad.append(f"{row.posix}: 名称超过 {MAX_NAME} 字节")
    return bad
