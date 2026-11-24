# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.

"""归档的打包与解包，按可用能力选择 zstd 或 gzip。

发行口径要求 zstd（06-backup-recovery.md §3.2）。Python 3.14 起标准库
自带 zstd；更老的解释器（CI 的 3.12）没有，此时用系统 zstd；两者都
不可用才退回 gzip，并把实际编码写进清单——编码是事实，不能假装。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any, Callable, Iterable

from .model import BadInput, BadIntegrity, BadStore, safe_arcname

try:  # Python >= 3.14
    from compression import zstd as _zstd  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - 依赖解释器版本
    _zstd = None

ZSTD_BIN = shutil.which("zstd")


def zstd_available() -> bool:
    return _zstd is not None or ZSTD_BIN is not None


def choose_codec(preferred: str = "zstd") -> str:
    """返回实际可用的编码名；auto 与 zstd 都优先 zstd。"""
    text = (preferred or "zstd").strip().lower()
    if text not in ("auto", "zstd", "gzip"):
        raise BadInput(f"未知压缩编码: {preferred!r}")
    if text == "gzip":
        return "gzip"
    return "zstd" if zstd_available() else "gzip"


def archive_name(stem: str, codec: str) -> str:
    return f"{stem}.tar.zst" if codec == "zstd" else f"{stem}.tar.gz"


def source_stats(path: Path) -> tuple[int, int, int]:
    """(文件数, 字节数, 直接子目录数)。

    直接子目录数只在来源指向 agents 目录时有意义，调用方据此判断是否
    记为 Agent 数；这里统一算出来，避免打包后再走一遍树。
    """
    if path.is_file() and not path.is_symlink():
        return 1, path.stat().st_size, 0
    files = 0
    size = 0
    children = 0
    for root, dirs, names in os.walk(path):
        if root == str(path):
            children = len(dirs)
        for name in names:
            full = Path(root) / name
            try:
                st = full.lstat()
            except OSError:
                continue
            if full.is_symlink():
                continue
            files += 1
            size += st.st_size
    return files, size, children


def _open_tar_writer(out: Path, codec: str, level: int):
    """返回 (tarfile, closer)；closer 必须在 tarfile 关闭之后调用。"""
    if codec == "gzip":
        tar = tarfile.open(out, mode="w:gz", compresslevel=9)
        return tar, lambda: None

    if _zstd is not None:
        raw = _zstd.open(out, "wb", level=level)
        tar = tarfile.open(fileobj=raw, mode="w")
        return tar, raw.close

    if ZSTD_BIN is None:
        raise BadStore("zstd 不可用且未指定 gzip")

    proc = subprocess.Popen(
        [ZSTD_BIN, "-q", "-T0", f"-{level}", "-f", "-o", str(out)],
        stdin=subprocess.PIPE,
    )
    assert proc.stdin is not None
    tar = tarfile.open(fileobj=proc.stdin, mode="w|")

    def close() -> None:
        try:
            proc.stdin.close()  # type: ignore[union-attr]
        finally:
            rc = proc.wait()
        if rc != 0:
            raise BadStore(f"zstd 压缩失败，退出码 {rc}")

    return tar, close


def pack(sources: Iterable[tuple[str, Path]], out_path: Path,
         codec: str = "zstd", level: int = 12) -> list[dict[str, Any]]:
    """把 (归档内相对路径, 源路径) 逐项加入归档。

    返回来源清单；任何一步失败都删除半成品，不把半份归档留在仓库里。
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    pairs: list[tuple[str, Path]] = []
    for name, path in sources:
        arcname = safe_arcname(name)
        src = Path(path)
        if not src.exists():
            raise BadInput(f"备份来源不存在: {src}")
        if not src.is_dir() and not src.is_file():
            raise BadInput(f"备份来源不是普通文件或目录: {src}")
        pairs.append((arcname, src))
        files, size, children = source_stats(src)
        entries.append({
            "name": arcname,
            "path": str(src),
            "files": files,
            "bytes": size,
            "agent_count": children if arcname.rstrip("/").endswith("agents") else 0,
        })

    if not pairs:
        raise BadInput("没有可备份的来源")

    tar, closer = _open_tar_writer(out_path, codec, level)
    try:
        for arcname, src in pairs:
            tar.add(str(src), arcname=arcname, recursive=True)
    except Exception:
        tar.close()
        closer()
        out_path.unlink(missing_ok=True)
        raise
    finally:
        if not tar.closed:
            tar.close()
    closer()
    return entries


def _open_tar_reader(path: Path, codec: str):
    if codec == "gzip":
        return tarfile.open(path, mode="r:gz"), (lambda: None)

    if _zstd is not None:
        raw = _zstd.open(path, "rb")
        return tarfile.open(fileobj=raw, mode="r|"), raw.close

    if ZSTD_BIN is None:
        raise BadStore("zstd 不可用，无法读取归档")

    proc = subprocess.Popen([ZSTD_BIN, "-dc", str(path)],
                            stdout=subprocess.PIPE)
    assert proc.stdout is not None
    tar = tarfile.open(fileobj=proc.stdout, mode="r|")

    def close() -> None:
        try:
            proc.stdout.close()  # type: ignore[union-attr]
        finally:
            rc = proc.wait()
        if rc != 0:
            raise BadIntegrity(f"zstd 解压失败，退出码 {rc}")

    return tar, close


def _check_member(member: tarfile.TarInfo) -> None:
    name = member.name.replace("\\", "/")
    if name.startswith("/"):
        raise BadIntegrity(f"归档成员是绝对路径: {member.name}")
    parts = name.split("/")
    if any(part == ".." for part in parts):
        raise BadIntegrity(f"归档成员含上跳路径: {member.name}")
    if not (member.isfile() or member.isdir() or
            member.issym() or member.islnk()):
        raise BadIntegrity(f"归档成员类型不受支持: {member.name}")


def unpack(path: Path, codec: str, target: Path,
           keep: Callable[[str], bool] | None = None) -> int:
    """解包到 target，返回写出的普通文件数。

    成员名先过路径检查，再交给 tarfile 的 data 过滤器；两层的含义不同：
    前者拦绝对路径与上跳，后者拦越界链接与设备节点。
    """
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    tar, closer = _open_tar_reader(Path(path), codec)
    files = 0
    try:
        for member in tar:
            _check_member(member)
            if keep is not None and not keep(member.name):
                continue
            if member.isfile():
                files += 1
            tar.extract(member, path=target, filter="data")
    finally:
        tar.close()
        closer()
    return files


def inspect(path: Path, codec: str) -> tuple[int, list[str]]:
    """只读遍历归档，返回 (普通文件数, 结构问题列表)。"""
    tar, closer = _open_tar_reader(Path(path), codec)
    files = 0
    problems: list[str] = []
    try:
        for member in tar:
            try:
                _check_member(member)
            except BadIntegrity as exc:
                problems.append(str(exc))
                continue
            if member.isfile():
                files += 1
    finally:
        tar.close()
        closer()
    return files, problems
