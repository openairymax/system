# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# deb 归档解析与安全解包
#
# 只认 ar 头与 tar 成员，不调用 dpkg/alien：debian-binary 必须是首成员且
# 声明 2.0，control 与 data 只接受 .gz/.xz/.zst 三种后缀。
#
# gz 与 xz 由标准库直接解，zst 交给 zstd -d；不额外引入 tar 命令行，是为了
# 让「先全量校验、再落盘」只有一套口径——成员路径、符号链接目标、硬链接
# 与设备节点在解包前一律判死，校验失败时目标目录仍是空的。zst 需要先落到
# 临时文件再解，因为 zstd 从标准输入读到的是整个 ar 区间而不是成员边界。

"""AirymaxOS deb archive reader."""

from __future__ import annotations

import contextlib
import io
import os
import posixpath
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .err import Fault

__all__ = [
    "Deb",
    "Member",
    "Script",
    "control_text",
    "open_deb",
    "scan",
    "scripts",
    "unpack",
]

MAGIC = b"!<arch>\n"

HEAD = 60

BIN = "debian-binary"

CTRL = "control.tar."

DATA = "data.tar."

MODES = {"gz": "r:gz", "xz": "r:xz"}

SCRIPT = ("preinst", "postinst", "prerm", "postrm")

NODES = (tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE)

LEGAL = (
    tarfile.REGTYPE,
    tarfile.AREGTYPE,
    tarfile.DIRTYPE,
    tarfile.SYMTYPE,
    tarfile.LNKTYPE,
)

FILTER = {"filter": "tar"} if sys.version_info >= (3, 11, 4) else {}


@dataclass(frozen=True, slots=True)
class Member:
    """ar 归档中的一个成员及其在文件里的字节区间。"""

    name: str
    offset: int
    size: int


@dataclass(frozen=True, slots=True)
class Script:
    """control 归档里的一个维护者脚本。"""

    name: str
    text: str


@dataclass(frozen=True, slots=True)
class Deb:
    """已通过结构校验的 deb，只保留需要用到的两个成员。"""

    path: Path
    control: Member
    data: Member


class Span(io.RawIOBase):
    """把 ar 成员区间包装成只读文件对象，避免整包读进内存。"""

    def __init__(self, handle: io.BufferedReader, item: Member) -> None:
        super().__init__()
        self._fd = handle.fileno()
        self._base = item.offset
        self._end = item.offset + item.size
        self._pos = item.offset

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos - self._base

    def readinto(self, buffer) -> int:
        want = min(len(buffer), self._end - self._pos)
        if want <= 0:
            return 0
        data = os.pread(self._fd, want, self._pos)
        buffer[:len(data)] = data
        self._pos += len(data)
        return len(data)

    def seek(self, pos: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_END:
            pos += self._end
        elif whence == io.SEEK_CUR:
            pos += self._pos
        else:
            pos += self._base
        self._pos = min(max(pos, self._base), self._end)
        return self._pos - self._base


def open_deb(path: Path) -> Deb:
    """校验 ar 结构与 debian-binary，返回只含 control/data 的 deb。"""
    path = Path(path)
    items = read_ar(path)
    _first(path, items)
    return Deb(path, _part(items, CTRL), _part(items, DATA))


def read_ar(path: Path) -> tuple[Member, ...]:
    """按 ar 头逐个切出成员；只支持 deb 实际使用的短名格式。"""
    with path.open("rb") as handle:
        if handle.read(len(MAGIC)) != MAGIC:
            raise Fault(f"不是 deb（ar 魔数不符）: {path}")
        return tuple(_walk(handle))


def _walk(handle: io.BufferedReader) -> Iterator[Member]:
    for head in iter(lambda: handle.read(HEAD), b""):
        name, size = _head(head)
        yield Member(name, handle.tell(), size)
        handle.seek(size + size % 2, io.SEEK_CUR)


def _head(raw: bytes) -> tuple[str, int]:
    if len(raw) < HEAD:
        raise Fault("ar 成员头被截断")
    name = raw[:16].decode("ascii", "replace").strip().rstrip("/")
    size = raw[48:58].decode("ascii", "replace").strip()
    if not size.isdigit():
        raise Fault(f"ar 成员长度不可读: {name or '?'}")
    return name, int(size)


def _first(path: Path, items: tuple[Member, ...]) -> None:
    if not items or items[0].name != BIN:
        raise Fault("首成员不是 debian-binary")
    if not _bytes(path, items[0]).startswith(b"2.0"):
        raise Fault("debian-binary 未声明 2.0")


def _bytes(path: Path, item: Member) -> bytes:
    with path.open("rb") as handle:
        handle.seek(item.offset)
        return handle.read(item.size)


def _part(items: tuple[Member, ...], prefix: str) -> Member:
    for item in items:
        if item.name.startswith(prefix):
            _mode(item.name)
            return item
    raise Fault(f"缺少 {prefix}* 成员")


def _mode(name: str) -> str:
    suffix = name.rsplit(".", 1)[-1]
    if suffix == "zst":
        return "zst"
    if suffix not in MODES:
        raise Fault(f"不支持的压缩格式: {name}")
    return MODES[suffix]


@contextlib.contextmanager
def _open(deb: Deb, item: Member) -> Iterator[tarfile.TarFile]:
    mode = _mode(item.name)
    with deb.path.open("rb") as handle:
        span = Span(handle, item)
        if mode == "zst":
            with _inflate(span) as raw:
                with tarfile.open(fileobj=raw, mode="r:") as tar:
                    yield tar
        else:
            with tarfile.open(fileobj=span, mode=mode) as tar:
                yield tar


@contextlib.contextmanager
def _inflate(span: Span) -> Iterator[io.BufferedRandom]:
    with tempfile.TemporaryFile() as packed, tempfile.TemporaryFile() as raw:
        _feed(span, packed)
        packed.seek(0)
        _zstd(packed, raw)
        raw.seek(0)
        yield raw


def _feed(span: Span, out) -> None:
    chunk = span.read(65536)
    while chunk:
        out.write(chunk)
        chunk = span.read(65536)


def _zstd(packed, raw) -> None:
    try:
        subprocess.run(["zstd", "-d", "-c"], stdin=packed, stdout=raw,
                       check=True)
    except FileNotFoundError as exc:
        raise Fault("缺少 zstd，请安装 zstd 包") from exc
    except subprocess.CalledProcessError as exc:
        raise Fault(f"zstd 解压失败（退出 {exc.returncode}）") from exc


def scan(deb: Deb) -> tuple[int, int]:
    """返回 data 归档的非目录成员数与解包后的常规文件总字节数。"""
    count = 0
    total = 0
    with _open(deb, deb.data) as tar:
        for info in tar:
            if info.isdir():
                continue
            count += 1
            total += info.size
    return count, total


def unpack(deb: Deb, dest: Path) -> None:
    """把 data 归档校验一遍后整体落到 dest；任一条越界即中止。"""
    dest = Path(dest)
    with _open(deb, deb.data) as tar:
        items = tar.getmembers()
        for info in items:
            _verify(info)
        dest.mkdir(parents=True, exist_ok=True)
        tar.extractall(dest, members=items, **FILTER)


def _verify(info: tarfile.TarInfo) -> None:
    _target(info, _rel(info.name))
    _kind(info)


def _rel(name: str) -> str:
    if name.startswith("/"):
        raise Fault(f"归档成员是绝对路径: {name}")
    return _inside(posixpath.normpath(name), name)


def _inside(clean: str, name: str) -> str:
    if clean == ".." or clean.startswith("../"):
        raise Fault(f"归档成员越出根目录: {name}")
    return clean


def _target(info: tarfile.TarInfo, clean: str) -> str:
    if info.issym():
        return _link(info, clean)
    if info.islnk():
        return _hard(info)
    return clean


def _link(info: tarfile.TarInfo, clean: str) -> str:
    if info.linkname.startswith("/"):
        raise Fault(f"符号链接指向绝对路径: {info.name}")
    target = posixpath.normpath(
        posixpath.join(posixpath.dirname(clean), info.linkname))
    return _inside(target, info.linkname)


def _hard(info: tarfile.TarInfo) -> str:
    if info.linkname.startswith("/"):
        raise Fault(f"硬链接指向绝对路径: {info.name}")
    return _inside(posixpath.normpath(info.linkname), info.linkname)


def _kind(info: tarfile.TarInfo) -> None:
    if info.type in NODES:
        raise Fault(f"归档含设备节点或管道: {info.name}")
    if info.type not in LEGAL:
        raise Fault(f"不支持的归档成员类型: {info.name}")


def control_text(deb: Deb) -> str:
    """读 control 归档里的 control 文件；不落盘。"""
    with _open(deb, deb.control) as tar:
        for info in tar:
            if _base(info) == "control" and info.isfile():
                return _text(tar, info)
    raise Fault("control 归档缺少 control 文件")


def scripts(deb: Deb) -> tuple[Script, ...]:
    """取出四个维护者脚本的全文，按 Debian 规定的顺序返回。"""
    found = []
    with _open(deb, deb.control) as tar:
        for info in tar:
            name = _base(info)
            if name in SCRIPT and info.isfile():
                found.append(Script(name, _text(tar, info)))
    return tuple(sorted(found, key=lambda item: SCRIPT.index(item.name)))


def _base(info: tarfile.TarInfo) -> str:
    return posixpath.basename(_rel(info.name))


def _text(tar: tarfile.TarFile, info: tarfile.TarInfo) -> str:
    handle = tar.extractfile(info)
    if handle is None:
        return ""
    return handle.read().decode("utf-8", "replace")
