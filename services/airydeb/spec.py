# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 由已解包的 deb 目录树生成最小 RPM 配方
#
# 每个 deb 现场生成一份 spec，不落仓库：Name 取 deb 包名（下划线保留，其余
# 非法字符换成 -），Version 取上游部分（去掉 epoch 与 Debian 修订号），
# Release 带 %{?dist} 与 .airydeb 后缀，装出来一眼能看出是转换产物。
#
# 依赖只做报告，不生成 Requires：Debian 与 RPM 的包名没有稳定对应关系，
# 硬映射会让本来能装的包因为名字对不上而失败。配方关闭自动依赖生成，安装
# 时不加 --nodeps，两者都避免把「名字不认识」变成事务失败。
#
# %files 只写显式文件路径，不用 /* 通配：通配会把 buildroot 里恰好存在的
# 其他包内容吞进来，症状是两个包同持一条路径。
#
# 目录一律不写 %dir。deb 的 data 归档会带上 ./usr/bin、./usr/share 这类父
# 目录，写成 %dir 就与 filesystem 包争同一条路径，安装事务在 test 阶段直接
# 失败（实测 "file /usr/bin ... conflicts with file from package
# filesystem"）。实机验证：不列目录时 rpmbuild 不报未打包文件，rpm 安装
# 时自行补齐缺失的父目录，因此只列文件既够用又不争所有权。

"""AirymaxOS deb to RPM recipe generation."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from . import control
from .err import Fault

__all__ = ["ARCHS", "entries", "legal", "meta", "text", "upstream"]

ARCHS = {"amd64": "x86_64", "arm64": "aarch64", "riscv64": "riscv64"}

BAD = re.compile(r"[^A-Za-z0-9._+_-]")

MACRO = "%{_prefix}/"


def legal(name: str) -> str:
    """把 deb 包名变成 rpm 合法 Name。"""
    return BAD.sub("-", name.strip())


def upstream(version: str) -> str:
    """去掉 epoch 与 Debian 修订号，只留上游版本。"""
    return (version.split(":", 1)[-1].rsplit("-", 1)[0] or "0").strip() or "0"


def arch(fields: dict[str, str]) -> str:
    """按 deb 架构映射 BuildArch，不认识的直接拒绝。"""
    raw = control.field(fields, "architecture").strip().lower()
    if raw not in ARCHS:
        raise Fault(f"不支持的 deb 架构: {raw or '未声明'}")
    return ARCHS[raw]


def meta(fields: dict[str, str]) -> tuple[str, str, str]:
    """返回 (Name, Version, BuildArch)。"""
    name = legal(control.field(fields, "package"))
    if not name:
        raise Fault("control 缺少 Package 字段")
    return name, upstream(control.field(fields, "version")), arch(fields)


def text(tree: Path, fields: dict[str, str]) -> str:
    """生成只含安装规则的最小 spec。"""
    name, version, build_arch = meta(fields)
    return _render(Path(tree), fields, name, version, build_arch)


def _render(tree: Path, fields: dict[str, str], name: str,
            version: str, build_arch: str) -> str:
    rows = [
        f"Name:           {name}",
        f"Version:        {version}",
        "Release:        1%{?dist}.airydeb",
        f"Summary:        {_summary(fields)}",
        "License:        unspecified",
        f"BuildArch:      {build_arch}",
        "AutoReqProv:    no",
        "%global debug_package %{nil}",
        "",
        "%description",
        _desc(fields),
        "",
        "%install",
        "rm -rf %{buildroot}",
        "mkdir -p %{buildroot}",
        f"cp -a {shlex.quote(str(tree))}/. %{{buildroot}}/",
        "",
        "%files",
        "%defattr(-, root, root, -)",
        *entries(tree),
        "",
    ]
    return "\n".join(rows)


def _desc(fields: dict[str, str]) -> str:
    raw = control.field(fields, "description")
    body = "\n".join(line.strip() for line in raw.splitlines()).strip()
    return _dollar(body or "Converted Debian package")


def _summary(fields: dict[str, str]) -> str:
    return _desc(fields).splitlines()[0][:80]


def _dollar(value: str) -> str:
    return value.replace("%", "%%")


def entries(tree: Path) -> tuple[str, ...]:
    """按路径排序输出 %files 行，目录不列。"""
    rows = []
    for path in sorted(tree.rglob("*")):
        line = _entry(path, path.relative_to(tree).as_posix())
        if line:
            rows.append(line)
    return tuple(rows)


def _entry(path: Path, rel: str) -> str:
    if path.is_dir() and not path.is_symlink():
        return ""
    return _macro("/" + rel)


def _macro(path: str) -> str:
    if path.startswith("/usr/"):
        return MACRO + _dollar(path[len("/usr/"):])
    return _dollar(path)
