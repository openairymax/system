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
from typing import Any, Iterable, Mapping

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
        if self.source is not None and event.source != self.source:
            return False
        if self.code is not None and event.code != self.code:
            return False
        if self.severity is not None and event.severity != self.severity:
            return False
        if self.labels:
            got = dict(event.labels)
            for key, val in self.labels:
                if got.get(key) != val:
                    return False
        return True


def parse_window(text: Any) -> int:
    if not isinstance(text, str):
        raise RuleError("window must be a string such as 5m")
    hit = _DUR.match(text.strip())
    if hit is None:
        raise RuleError(f"bad window: {text!r}")
    span = int(hit.group(1)) * _SCALE[hit.group(2)]
    if not WINDOW_MIN_NS <= span <= WINDOW_MAX_NS:
        raise RuleError(f"window out of range: {text}")
    return span


def _label_pairs(value: Any) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise RuleError("match.labels must be an object")
    if len(value) > LABEL_MAX:
        raise RuleError(f"too many match labels: {len(value)}")
    pairs = []
    for key, val in value.items():
        if not isinstance(key, str) or not isinstance(val, str):
            raise RuleError("match.labels must map strings to strings")
        if not key or len(key.encode("utf-8")) > 32:
            raise RuleError(f"bad match label key: {key!r}")
        if len(val.encode("utf-8")) > 64:
            raise RuleError(f"match label value too long: {key}")
        pairs.append((key, val))
    return tuple(sorted(pairs))


def _parse_match(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise RuleError("match must be an object")
    extra = set(value) - _MATCH_KEYS
    if extra:
        raise RuleError(f"unknown match keys: {sorted(extra)}")
    out: dict[str, Any] = {}
    if "source" in value:
        try:
            out["source"] = source_of(value["source"])
        except BadEvent as exc:
            raise RuleError(str(exc)) from exc
    if "code" in value:
        code = value["code"]
        if isinstance(code, bool) or not isinstance(code, int):
            raise RuleError("match.code must be an integer")
        if not 0 <= code <= CODE_MAX:
            raise RuleError(f"match.code out of range: {code}")
        out["code"] = code
    if "severity" in value:
        try:
            out["severity"] = Severity.parse(value["severity"])
        except BadEvent as exc:
            raise RuleError(str(exc)) from exc
    out["labels"] = _label_pairs(value.get("labels"))
    return out


def _parse_rule(obj: Any, where: str) -> Rule:
    if not isinstance(obj, Mapping):
        raise RuleError(f"{where}: rule must be an object")
    extra = set(obj) - _RULE_KEYS
    if extra:
        raise RuleError(f"{where}: unknown keys: {sorted(extra)}")
    name = obj.get("name")
    if not isinstance(name, str) or _NAME.match(name) is None:
        raise RuleError(f"{where}: bad name: {name!r}")
    window = parse_window(obj.get("window"))
    emit = obj.get("max_emit", 1)
    if isinstance(emit, bool) or not isinstance(emit, int):
        raise RuleError(f"{where}: max_emit must be an integer")
    if not 0 <= emit <= EMIT_MAX:
        raise RuleError(f"{where}: max_emit out of range: {emit}")
    note = obj.get("note", "")
    if not isinstance(note, str):
        raise RuleError(f"{where}: note must be a string")
    if len(note.encode("utf-8")) > NOTE_MAX:
        raise RuleError(f"{where}: note too long")
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
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuleError(f"{path}: {exc}") from exc
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuleError(f"{path}: {exc}") from exc
    if not isinstance(doc, Mapping):
        raise RuleError(f"{path}: document must be an object")
    extra = set(doc) - _DOC_KEYS
    if extra:
        raise RuleError(f"{path}: unknown keys: {sorted(extra)}")
    if doc.get("schema", 1) != 1:
        raise RuleError(f"{path}: unsupported schema")
    rows = doc.get("rules")
    if not isinstance(rows, list):
        raise RuleError(f"{path}: rules must be an array")
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
                if rule.name in seen:
                    raise RuleError(f"duplicate rule name: {rule.name}")
                seen.add(rule.name)
                out.append(rule)
    if not found:
        raise RuleError("no rules directory found")
    return tuple(out)
