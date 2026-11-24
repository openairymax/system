# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.

"""备份仓库：目录布局、清单索引、核验、恢复与保留期清理。

仓库根下每个类型一个目录，归档与它的 .sha256 / .sig / 单件清单并排；
根上的 manifest.json 是给人和巡检脚本看的索引，读路径始终以单件清单
为准——索引可以重建，单件清单是事实。
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from . import archive
from .model import (
    BadInput, BadIntegrity, BadStore, BackupRecord, Kind, Retention, SCHEMA,
    VERSION, day_key, iso, make_id, member_under_agent,
)

CHUNK = 1024 * 1024
DEFAULT_ROOT = Path("/var/lib/airy/backup")
DEFAULT_LOG = Path("/var/log/airy/recovery.log")

# 06-backup-recovery.md §2.1 的对象清单；不存在的路径在创建时跳过并
# 记入来源表的 missing，而不是让整次备份失败——刚装好的机器上 L4 与
# 事件台账都可能还没有。
DEFAULT_SOURCES: tuple[tuple[str, str], ...] = (
    ("var/lib/airy/agents", "/var/lib/airy/agents"),
    ("var/lib/airy/memory/l3", "/var/lib/airy/memory/l3"),
    ("var/lib/airy/memory/l4", "/var/lib/airy/memory/l4"),
    ("etc/agentrt", "/etc/agentrt"),
    ("var/lib/airy/monitor", "/var/lib/airy/monitor"),
    ("var/lib/airy/incidents", "/var/lib/airy/incidents"),
    ("var/log/airy", "/var/log/airy"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class VerifyResult:
    backup_id: str
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})

    @property
    def ok(self) -> bool:
        return all(item["ok"] for item in self.checks)

    @property
    def errors(self) -> list[str]:
        return [f"{c['check']}: {c['detail']}" for c in self.checks
                if not c["ok"]]

    def to_dict(self) -> dict[str, Any]:
        return {"backup_id": self.backup_id, "ok": self.ok,
                "checks": self.checks, "errors": self.errors}


class Store:
    def __init__(self, root: Path | str = DEFAULT_ROOT,
                 retention: Retention | None = None) -> None:
        self.root = Path(root)
        self.retention = retention or Retention()

    # ── 布局 ─────────────────────────────────────────────────────────
    @property
    def index_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def lock_path(self) -> Path:
        return self.root / ".backup_lock"

    def _ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for kind in Kind:
            (self.root / kind.value).mkdir(exist_ok=True)

    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        self._ensure()
        with open(self.lock_path, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _stem(kind: Kind, stamp_ns: int, seq: int) -> str:
        return f"{kind.value}_{day_key(stamp_ns)}-{seq:03d}"

    def path_of(self, record: BackupRecord) -> Path:
        return self.root / record.kind.value / record.file

    def _manifest_of(self, record: BackupRecord) -> Path:
        stem = Path(record.file).name
        for suffix in (".tar.zst", ".tar.gz"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        return self.root / record.kind.value / f"{stem}.manifest.json"

    def _checksum_of(self, record: BackupRecord) -> Path:
        return self.path_of(record).with_name(record.file + ".sha256")

    def _signature_of(self, record: BackupRecord) -> Path:
        return self.path_of(record).with_name(record.file + ".sig")

    # ── 读取 ─────────────────────────────────────────────────────────
    def records(self) -> list[BackupRecord]:
        found: list[BackupRecord] = []
        for kind in Kind:
            folder = self.root / kind.value
            if not folder.is_dir():
                continue
            for item in folder.glob("*.manifest.json"):
                try:
                    data = json.loads(item.read_text(encoding="utf-8"))
                    found.append(BackupRecord.from_dict(data))
                except (OSError, ValueError) as exc:
                    raise BadStore(f"清单不可读: {item}: {exc}") from exc
        found.sort(key=lambda rec: (rec.created_at, rec.backup_id))
        return found

    def ids(self) -> list[str]:
        return [rec.backup_id for rec in self.records()]

    def load(self, backup_id: str) -> BackupRecord:
        for rec in self.records():
            if rec.backup_id == backup_id:
                return rec
        raise BadStore(f"备份不存在: {backup_id}")

    def latest(self, kind: Kind | None = None) -> BackupRecord | None:
        recs = self.records()
        if kind is not None:
            want = Kind.parse(kind)
            recs = [rec for rec in recs if rec.kind is want]
        return recs[-1] if recs else None

    # ── 创建 ─────────────────────────────────────────────────────────
    def create(self, kind: Kind | str, sources: list[tuple[str, Path]] | None = None,
               stamp: int | None = None, actor: str = "",
               sign_key: Path | str | None = None, codec: str = "zstd",
               previous: str | None = None) -> BackupRecord:
        kind = Kind.parse(kind)
        stamp = time.time_ns() if stamp is None else int(stamp)
        pairs = list(sources) if sources is not None else self._default_pairs()

        with self._lock():
            recs = self.records()
            same_day = [rec for rec in recs
                        if rec.kind is kind and day_key(rec.created_at) == day_key(stamp)]
            seq = max((int(rec.backup_id.rsplit("-", 1)[1]) for rec in same_day),
                      default=0) + 1
            backup_id = make_id(kind, stamp, seq)
            stem = self._stem(kind, stamp, seq)
            chosen = archive.choose_codec(codec)
            file_name = archive.archive_name(stem, chosen)
            target = self.root / kind.value / file_name
            partial = target.with_name(file_name + ".partial")

            entries = archive.pack(pairs, partial, codec=chosen)
            try:
                digest = sha256_file(partial)
                size = partial.stat().st_size
                os.replace(partial, target)
            finally:
                partial.unlink(missing_ok=True)

            signed_by = ""
            if sign_key:
                self._sign(target, Path(sign_key))
                signed_by = actor or "operator"

            self._checksum_of_path(target).write_text(
                f"{digest}  {file_name}\n", encoding="utf-8")

            prev = previous
            if prev is None:
                older = [rec for rec in recs if rec.kind is kind]
                prev = older[-1].backup_id if older else ""

            record = BackupRecord(
                backup_id=backup_id,
                kind=kind,
                created_at=stamp,
                completed_at=max(stamp, time.time_ns()),
                file=file_name,
                compression=chosen,
                size_bytes=size,
                sha256=digest,
                actor=actor,
                signed_by=signed_by,
                agent_count=sum(int(e.get("agent_count", 0)) for e in entries),
                config_files=sum(int(e.get("files", 0)) for e in entries
                                 if str(e.get("name", "")).startswith("etc/")),
                total_files=sum(int(e.get("files", 0)) for e in entries),
                sources=entries,
                previous_backup_id=prev,
            )
            self._write_manifest(record)
            self._write_index()
            return record

    def _default_pairs(self) -> list[tuple[str, Path]]:
        pairs: list[tuple[str, Path]] = []
        for name, path in DEFAULT_SOURCES:
            src = Path(path)
            if src.exists():
                pairs.append((name, src))
        if not pairs:
            raise BadInput("默认备份对象都不存在；请用 --source 指定来源")
        return pairs

    # ── 签名 ─────────────────────────────────────────────────────────
    def _sign(self, target: Path, key: Path) -> None:
        if not key.is_file():
            raise BadInput(f"签名私钥不存在: {key}")
        if shutil.which("openssl") is None:
            raise BadInput("openssl 不可用，无法签名")
        proc = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(key),
             "-out", str(target.with_name(target.name + ".sig")), str(target)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            target.with_name(target.name + ".sig").unlink(missing_ok=True)
            raise BadStore(f"签名失败: {proc.stderr.strip()}")

    # ── 清单 ─────────────────────────────────────────────────────────
    @staticmethod
    def _checksum_of_path(target: Path) -> Path:
        return target.with_name(target.name + ".sha256")

    def _write_manifest(self, record: BackupRecord) -> None:
        path = self._manifest_of(record)
        path.write_text(json.dumps(record.to_dict(), ensure_ascii=False,
                                   indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")

    def _write_index(self) -> None:
        recs = self.records()
        body = {
            "schema": SCHEMA,
            "version": VERSION,
            "generated_at": iso(time.time_ns()),
            "backups": [rec.to_dict() for rec in recs],
        }
        self.index_path.write_text(
            json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")

    # ── 核验 ─────────────────────────────────────────────────────────
    def verify(self, backup_id: str, pub_key: Path | str | None = None,
               deep: bool = False) -> VerifyResult:
        result = VerifyResult(backup_id=backup_id)
        try:
            rec = self.load(backup_id)
        except BadStore as exc:
            result.add("manifest", False, str(exc))
            return result
        result.add("manifest", True, rec.kind.value)

        target = self.path_of(rec)
        if not target.is_file():
            result.add("archive", False, f"缺少归档 {target}")
            return result
        result.add("archive", True, target.name)

        digest = sha256_file(target)
        result.add("sha256", digest == rec.sha256,
                   "" if digest == rec.sha256 else f"{digest} != {rec.sha256}")
        size = target.stat().st_size
        result.add("size", size == rec.size_bytes,
                   "" if size == rec.size_bytes else f"{size} != {rec.size_bytes}")

        check_file = self._checksum_of(rec)
        if check_file.is_file():
            token = check_file.read_text(encoding="utf-8").split()
            result.add("checksum_file", bool(token) and token[0] == rec.sha256,
                       str(check_file))
        else:
            result.add("checksum_file", False, f"缺少 {check_file.name}")

        sig = self._signature_of(rec)
        if rec.signed_by:
            if not sig.is_file():
                result.add("signature", False, "清单声明已签名但缺少 .sig")
            elif pub_key is None:
                result.add("signature", False, "已签名，但未提供公钥")
            elif shutil.which("openssl") is None:
                result.add("signature", False, "openssl 不可用，无法验签")
            else:
                proc = subprocess.run(
                    ["openssl", "dgst", "-sha256", "-verify", str(pub_key),
                     "-signature", str(sig), str(target)],
                    capture_output=True, text=True)
                result.add("signature", proc.returncode == 0,
                           proc.stdout.strip() or proc.stderr.strip())
        elif sig.is_file():
            result.add("signature", False, "存在 .sig 但清单未声明签名")
        else:
            result.add("signature", True, "未签名")

        if deep:
            try:
                files, problems = archive.inspect(target, rec.compression)
            except (OSError, BadStore) as exc:
                result.add("structure", False, str(exc))
            else:
                result.add("structure", not problems,
                           "; ".join(problems) or f"{files} files")
        return result

    def verify_all(self, pub_key: Path | str | None = None,
                   deep: bool = False) -> dict[str, dict[str, Any]]:
        return {rec.backup_id: self.verify(rec.backup_id, pub_key=pub_key,
                                           deep=deep).to_dict()
                for rec in self.records()}

    # ── 恢复 ─────────────────────────────────────────────────────────
    def restore(self, backup_id: str, target: Path | str, stamp: int | None = None,
                actor: str = "", agent_id: str | int | None = None,
                confirm: bool = False, pub_key: Path | str | None = None,
                log_path: Path | str | None = None) -> dict[str, Any]:
        rec = self.load(backup_id)
        result = self.verify(backup_id, pub_key=pub_key, deep=True)
        if not result.ok:
            raise BadIntegrity("；".join(result.errors))

        if rec.kind is Kind.FULL and not confirm:
            raise BadInput("全量恢复会覆盖现有数据，必须加 --confirm")

        dest = Path(target)
        if dest.exists() and not dest.is_dir():
            raise BadInput(f"恢复目标不是目录: {dest}")
        dest.mkdir(parents=True, exist_ok=True)

        staging = dest / f".airy-restore-{rec.backup_id}"
        if staging.exists():
            shutil.rmtree(staging)
        keep = None
        if agent_id is not None:
            keep = lambda name: member_under_agent(name, agent_id)  # noqa: E731
        files = archive.unpack(self.path_of(rec), rec.compression, staging, keep=keep)
        if agent_id is not None and files == 0:
            shutil.rmtree(staging, ignore_errors=True)
            raise BadInput(f"备份中没有 Agent {agent_id} 的数据")

        for child in list(staging.iterdir()):
            self._merge(child, dest / child.name)
        staging.rmdir()

        when = time.time_ns() if stamp is None else int(stamp)
        record = {
            "at": iso(when),
            "event": "restore",
            "backup_id": rec.backup_id,
            "target": str(dest),
            "actor": actor,
            "agent_id": "" if agent_id is None else str(agent_id),
            "files": files,
            "kind": rec.kind.value,
        }
        self._log(log_path, record)
        return record

    @staticmethod
    def _merge(src: Path, dst: Path) -> None:
        if src.is_dir():
            if dst.exists() and not dst.is_dir():
                dst.unlink()
            shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True)
            shutil.rmtree(src)
        else:
            if dst.is_dir():
                shutil.rmtree(dst)
            os.replace(src, dst)

    @staticmethod
    def _log(log_path: Path | str | None, record: dict[str, Any]) -> None:
        path = Path(log_path) if log_path else DEFAULT_LOG
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False,
                                        sort_keys=True) + "\n")
        except OSError as exc:
            raise BadStore(f"恢复日志写入失败: {path}: {exc}") from exc

    # ── 保留期 ───────────────────────────────────────────────────────
    def prune(self, stamp: int | None = None, apply: bool = False) -> dict[str, Any]:
        now = time.time_ns() if stamp is None else int(stamp)
        with self._lock():
            recs = self.records()
            newest_full = None
            fulls = [rec for rec in recs if rec.kind is Kind.FULL]
            if fulls:
                newest_full = max(fulls, key=lambda rec: rec.created_at)

            candidates: list[BackupRecord] = []
            for rec in recs:
                if newest_full is not None and rec.backup_id == newest_full.backup_id:
                    continue  # 任何时候至少留一份全量，清空仓库不是保留策略
                if rec.created_at < self.retention.cutoff(rec.kind, now):
                    candidates.append(rec)

            removed: list[str] = []
            if apply:
                for rec in candidates:
                    self.path_of(rec).unlink(missing_ok=True)
                    self._checksum_of(rec).unlink(missing_ok=True)
                    self._signature_of(rec).unlink(missing_ok=True)
                    self._manifest_of(rec).unlink(missing_ok=True)
                    removed.append(rec.backup_id)
                self._write_index()

            return {
                "checked": len(recs),
                "candidates": [rec.backup_id for rec in candidates],
                "removed": removed,
                "applied": bool(apply),
            }

    # ── 统计 ─────────────────────────────────────────────────────────
    def stats(self) -> dict[str, Any]:
        recs = self.records()
        by_kind: dict[str, dict[str, Any]] = {}
        for kind in Kind:
            subset = [rec for rec in recs if rec.kind is kind]
            by_kind[kind.value] = {
                "count": len(subset),
                "bytes": sum(rec.size_bytes for rec in subset),
                "latest": subset[-1].backup_id if subset else "",
            }
        return {
            "root": str(self.root),
            "total": len(recs),
            "bytes": sum(rec.size_bytes for rec in recs),
            "kinds": by_kind,
        }
