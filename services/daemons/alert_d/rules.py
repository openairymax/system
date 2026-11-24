# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 告警抑制规则
#
# 规则是 JSON 文档：顶层 schema 与 rules 两个键，每条规则给出名称、窗口、
# 窗口内放行条数与匹配条件。加载时逐字段校验并拒绝未知键，避免把拼写
# 错误当成「没有规则」静默放过；重名同样拒绝，防止两个文件互相覆盖。

"""Suppression rule loading and matching."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from airylib.err import need

from .model import (
    CODE_MAX, NS, BadEvent, Severity, source_of,
)

WINDOW_MIN_NS = NS
WINDOW_MAX_NS = 24 * 60 * 60 * NS
EMIT_MAX = 10000
NOTE_MAX = 96
NAME_MAX = 32
LABEL_MAX = 8

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_DUR = re.compile(r"^([0-9]{1,5})([smh])$")
_SCALE = {"s": NS, "m": 60 * NS, "h": 60 * 60 * NS}
_DOC_KEYS = frozenset({"schema", "rules"})
_RULE_KEYS = frozenset({"name", "window", "max_emit", "match", "note"})
_MATCH_KEYS = frozenset({"source", "code", "severity", "labels"})


class RuleError(ValueError):
    """Rule document or directory is not usable."""


@dataclass(frozen=True)
class Rule:
    name: str
    window_ns: int
    max_emit: int
    source: int | None = None
    code: int | None = None
    severity: Severity | None = None
    labels: tuple[tuple[str, str], ...] = ()
    note: str = ""

    def match(self, event: Any) -> bool:
        """逐项比对非空条件，标签按子集匹配。"""
        conds = ((self.source, event.source), (self.code, event.code),
                 (self.severity, event.severity))
        if any(want is not None and want != got for want, got in conds):
            return False
        got = dict(event.labels)
        return all(got.get(key) == val for key, val in self.labels)


def _wrap(fn: Callable[[Any], Any], value: Any) -> Any:
    """把模型层的字段错误转成规则错误，消息保持不变。"""
    try:
        return fn(value)
    except BadEvent as exc:
        raise RuleError(str(exc)) from exc


def parse_window(text: Any) -> int:
    """把 5s/30m/1h 解析成纳秒，并检查窗口范围。"""
    need(isinstance(text, str), "window must be a string such as 5m", RuleError)
    hit = _DUR.match(text.strip())
    need(hit is not None, f"bad window: {text!r}", RuleError)
    span = int(hit.group(1)) * _SCALE[hit.group(2)]
    need(WINDOW_MIN_NS <= span <= WINDOW_MAX_NS,
         f"window out of range: {text}", RuleError)
    return span


def _label_of(key: Any, val: Any) -> tuple[str, str]:
    need(isinstance(key, str) and isinstance(val, str),
         "match.labels must map strings to strings", RuleError)
    need(key and len(key.encode("utf-8")) <= 32,
         f"bad match label key: {key!r}", RuleError)
    need(len(val.encode("utf-8")) <= 64,
         f"match label value too long: {key}", RuleError)
    return key, val


def _label_pairs(value: Any) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    need(isinstance(value, Mapping), "match.labels must be an object", RuleError)
    need(len(value) <= LABEL_MAX,
         f"too many match labels: {len(value)}", RuleError)
    return tuple(sorted(_label_of(key, val) for key, val in value.items()))


def _parse_match(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    need(isinstance(value, Mapping), "match must be an object", RuleError)
    extra = set(value) - _MATCH_KEYS
    need(not extra, f"unknown match keys: {sorted(extra)}", RuleError)
    out: dict[str, Any] = {}
    if "source" in value:
        out["source"] = _wrap(source_of, value["source"])
    if "code" in value:
        code = value["code"]
        need(isinstance(code, int) and not isinstance(code, bool),
             "match.code must be an integer", RuleError)
        need(0 <= code <= CODE_MAX, f"match.code out of range: {code}", RuleError)
        out["code"] = code
    if "severity" in value:
        out["severity"] = _wrap(Severity.parse, value["severity"])
    out["labels"] = _label_pairs(value.get("labels"))
    return out


def _parse_rule(obj: Any, where: str) -> Rule:
    need(isinstance(obj, Mapping), f"{where}: rule must be an object", RuleError)
    extra = set(obj) - _RULE_KEYS
    need(not extra, f"{where}: unknown keys: {sorted(extra)}", RuleError)
    name = obj.get("name")
    need(isinstance(name, str) and _NAME.match(name) is not None,
         f"{where}: bad name: {name!r}", RuleError)
    window = parse_window(obj.get("window"))
    emit = obj.get("max_emit", 1)
    need(isinstance(emit, int) and not isinstance(emit, bool),
         f"{where}: max_emit must be an integer", RuleError)
    need(0 <= emit <= EMIT_MAX, f"{where}: max_emit out of range: {emit}", RuleError)
    note = obj.get("note", "")
    need(isinstance(note, str), f"{where}: note must be a string", RuleError)
    need(len(note.encode("utf-8")) <= NOTE_MAX, f"{where}: note too long", RuleError)
    match = _parse_match(obj.get("match"))
    return Rule(
        name=name,
        window_ns=window,
        max_emit=emit,
        source=match.get("source"),
        code=match.get("code"),
        severity=match.get("severity"),
        labels=match.get("labels", ()),
        note=note,
    )


def _load_file(path: Path) -> tuple[Rule, ...]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuleError(f"{path}: {exc}") from exc
    need(isinstance(doc, Mapping), f"{path}: document must be an object", RuleError)
    extra = set(doc) - _DOC_KEYS
    need(not extra, f"{path}: unknown keys: {sorted(extra)}", RuleError)
    need(doc.get("schema", 1) == 1, f"{path}: unsupported schema", RuleError)
    rows = doc.get("rules")
    need(isinstance(rows, list), f"{path}: rules must be an array", RuleError)
    return tuple(
        _parse_rule(row, f"{path.name}#{idx}") for idx, row in enumerate(rows)
    )


def load_rules(paths: Iterable[str | Path] | str | Path) -> tuple[Rule, ...]:
    if isinstance(paths, (str, Path)):
        paths = (paths,)
    out: list[Rule] = []
    seen: set[str] = set()
    found = False
    for item in paths:
        base = Path(item)
        if not base.is_dir():
            continue
        found = True
        for path in sorted(base.glob("*.json")):
            for rule in _load_file(path):
                need(rule.name not in seen,
                     f"duplicate rule name: {rule.name}", RuleError)
                seen.add(rule.name)
                out.append(rule)
    need(found, "no rules directory found", RuleError)
    return tuple(out)
