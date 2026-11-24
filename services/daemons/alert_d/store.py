# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# AirymaxOS 告警持久化
#
# 活动告警写单个 active.json，生命周期迁移追加到 archive/<UTC 日期>.jsonl，
# 运行状态写 status.json。三份文件都用临时文件加 rename 落盘，读者永远看到
# 完整的一份；目录由调用方给出，测试与装机后的默认根一致。

"""Alert state and archive persistence."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA = 1
DIR_MODE = 0o750
FILE_MODE = 0o640
ACTIVE = "active.json"
STATUS = "status.json"
ARCHIVE = "archive"
# 活动列表按严重度倒序，级别必须先折算成数值：直接比较 "WARNING" 与
# "CRITICAL" 的字面序会把 WARNING 排到最前。
RANK = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


class Store:
    """Filesystem backed alert state."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.archive = self.root / ARCHIVE

    def _ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        self.archive.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)

    def _write(self, path: Path, text: str) -> None:
        self._ensure()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, FILE_MODE)
        os.replace(tmp, path)

    def save(self, alerts: Iterable[Any]) -> None:
        rows = [alert.to_json() for alert in alerts]
        rows.sort(key=lambda row: (RANK.get(row["severity"], 0), row["first_ns"]),
                  reverse=True)
        text = _dump({"schema": SCHEMA, "alerts": rows}) + "\n"
        self._write(self.root / ACTIVE, text)

    def load(self) -> list[dict[str, Any]]:
        path = self.root / ACTIVE
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"{path}: {exc}") from exc
        if not isinstance(doc, Mapping) or doc.get("schema") != SCHEMA:
            raise ValueError(f"{path}: unsupported schema")
        rows = doc.get("alerts")
        if not isinstance(rows, list):
            raise ValueError(f"{path}: alerts must be an array")
        return rows

    def save_status(self, status: Mapping[str, Any]) -> None:
        self._write(self.root / STATUS, _dump(status) + "\n")

    def status(self) -> dict[str, Any]:
        path = self.root / STATUS
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"{path}: {exc}") from exc
        if not isinstance(doc, dict):
            raise ValueError(f"{path}: status must be an object")
        return doc

    def append(self, alert: Any) -> Path:
        # 归档按告警首次出现的墙钟日期分片。first_ns 是引擎的单调时钟，
        # 只用于计算窗口，换算成日期没有意义。
        stamp = alert.event.ts_ns or alert.first_ns
        secs = max(stamp, 0) / 1_000_000_000
        day = time.strftime("%Y%m%d", time.gmtime(secs))
        path = self.archive / f"{day}.jsonl"
        self._ensure()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(_dump(alert.to_json()) + "\n")
        os.chmod(path, FILE_MODE)
        return path

    def history(
        self,
        severity: str | None = None,
        source: str | None = None,
        agent: int | None = None,
        code: int | None = None,
        since_ns: int = 0,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        if not self.archive.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for path in sorted(self.archive.glob("*.jsonl")):
            try:
                handle = path.open("r", encoding="utf-8")
            except OSError as exc:
                raise ValueError(f"{path}: {exc}") from exc
            with handle:
                for lineno, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"{path}:{lineno}: {exc}") from exc
                    if not self._keep(row, severity, source, agent, code, since_ns):
                        continue
                    out.append(row)
                    if len(out) >= limit:
                        return out
        return out

    @staticmethod
    def _keep(
        row: Mapping[str, Any],
        severity: str | None,
        source: str | None,
        agent: int | None,
        code: int | None,
        since_ns: int,
    ) -> bool:
        if severity is not None and row.get("severity") != severity.upper():
            return False
        if source is not None and row.get("source") != source:
            return False
        if agent is not None and row.get("agent") != agent:
            return False
        if code is not None and row.get("code") != code:
            return False
        return int(row.get("ts_ns", 0)) >= since_ns
