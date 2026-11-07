# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# airy-deb 命令行
#
# info 只读打印元数据；install 现场转成 RPM 后交给 dnf；convert 只产出 RPM。
# 三条路径共用 archive 的结构校验与 plan 的脚本取舍，命令行只做分派。
#
# 退出码沿用 airy-bright 的分层：0 成功；2 用法错误；4 环境不具备（缺工具、
# 平台不支持、文件不是 deb）；5 策略拒绝（带维护者脚本但没给 --ignore-scripts）；
# 6 构建或安装失败。4 与 5 分开，是为了让「机器上没装 rpm-build」和「这个包
# 带了 Debian 维护者脚本」在日志里一眼能分辨。

"""AirymaxOS deb helper command line."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from . import archive, control, plan, spec
from .err import (
    EXIT_BUILD,
    EXIT_ENV,
    EXIT_OK,
    EXIT_POLICY,
    EXIT_USAGE,
    Fault,
)

__all__ = [
    "EXIT_BUILD",
    "EXIT_ENV",
    "EXIT_OK",
    "EXIT_POLICY",
    "EXIT_USAGE",
    "main",
]

RPMBUILD = ("rpmbuild", "rpm-build")

DNF = ("dnf", "dnf")


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行并分派；返回值即进程退出码。"""
    args = _args(argv)
    try:
        return _run(args)
    except Fault as exc:
        print(f"airy-deb: {exc}", file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(f"airy-deb: {exc}", file=sys.stderr)
        return EXIT_ENV


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="airy-deb",
        description="AirymaxOS Debian 包解析、转换与安装",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    info = sub.add_parser("info", help="解析 deb 并打印元数据")
    info.add_argument("file")
    put = sub.add_parser("install", help="转成 RPM 并安装")
    put.add_argument("file")
    put.add_argument("--ignore-scripts", action="store_true",
                     help="允许跳过 Debian 维护者脚本")
    put.add_argument("--dry-run", action="store_true",
                     help="只打印将要执行的命令")
    conv = sub.add_parser("convert", help="只转换出 RPM")
    conv.add_argument("file")
    conv.add_argument("-o", "--output", default=".", help="RPM 输出目录")
    return parser.parse_args(argv)


def _run(args: argparse.Namespace) -> int:
    deb = archive.open_deb(Path(args.file))
    fields = control.parse(archive.control_text(deb))
    if args.cmd == "info":
        return _info(deb, fields)
    if args.cmd == "convert":
        return _convert(deb, fields, Path(args.output))
    return _install(deb, fields, args)


def _info(deb: archive.Deb, fields: dict[str, str]) -> int:
    count, size = archive.scan(deb)
    print(f"package:      {control.field(fields, 'package')}")
    print(f"version:      {control.field(fields, 'version')}")
    print(f"architecture: {control.field(fields, 'architecture')}")
    print(f"depends:      {_joined(fields, 'depends')}")
    print(f"recommends:   {_joined(fields, 'recommends')}")
    print(f"scripts:      {_named(archive.scripts(deb))}")
    print(f"members:      {count}")
    print(f"bytes:        {size}")
    return EXIT_OK


def _joined(fields: dict[str, str], name: str) -> str:
    return ", ".join(control.items(fields, name)) or "-"


def _named(found: tuple[archive.Script, ...]) -> str:
    return ", ".join(item.name for item in found) or "none"


def _convert(deb: archive.Deb, fields: dict[str, str], out: Path) -> int:
    found = archive.scripts(deb)
    if found:
        print("airy-deb: 维护者脚本不会写入 RPM：", file=sys.stderr)
        print(plan.render(found), file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix="airy-deb-") as tmp:
        rpm = _build(deb, fields, Path(tmp))
        out.mkdir(parents=True, exist_ok=True)
        target = out / rpm.name
        shutil.copy2(rpm, target)
    print(target)
    return EXIT_OK


def _install(deb: archive.Deb, fields: dict[str, str],
             args: argparse.Namespace) -> int:
    found = archive.scripts(deb)
    if plan.blocked(found, args.ignore_scripts):
        _refuse(found)
        return EXIT_POLICY
    _notice(found, fields)
    name, version, _ = spec.meta(fields)
    if args.dry_run:
        _preview(name, version)
        return EXIT_OK
    with tempfile.TemporaryDirectory(prefix="airy-deb-") as tmp:
        _dnf(_build(deb, fields, Path(tmp)))
    return EXIT_OK


def _refuse(found: tuple[archive.Script, ...]) -> None:
    print("airy-deb: 拒绝安装：deb 含维护者脚本，RPM 不会执行它们",
          file=sys.stderr)
    print(plan.render(found), file=sys.stderr)
    print("airy-deb: 确认可以跳过请加 --ignore-scripts", file=sys.stderr)


def _notice(found: tuple[archive.Script, ...], fields: dict[str, str]) -> None:
    for line in plan.deps(fields):
        print(f"airy-deb: {line}（不自动映射为 RPM Requires）",
              file=sys.stderr)
    if found:
        print("airy-deb: 已跳过维护者脚本：", file=sys.stderr)
        print(plan.render(found), file=sys.stderr)


def _preview(name: str, version: str) -> None:
    print(f"package: {name} {version}")
    print("+ rpmbuild -bb --define '_topdir <work>' <work>/package.spec")
    print(f"+ dnf install -y <work>/RPMS/*/{name}-{version}-*.rpm")


def _build(deb: archive.Deb, fields: dict[str, str], work: Path) -> Path:
    _need(*RPMBUILD)
    tree = work / "tree"
    archive.unpack(deb, tree)
    (work / "package.spec").write_text(spec.text(tree, fields),
                                       encoding="utf-8")
    code, log = _quiet(["rpmbuild", "-bb", "--define", f"_topdir {work}",
                        str(work / "package.spec")])
    if code != 0:
        print(log, file=sys.stderr)
        raise Fault(f"rpmbuild 失败（退出 {code}）", EXIT_BUILD)
    return _find(work, spec.meta(fields))


def _find(work: Path, names: tuple[str, str, str]) -> Path:
    name, version, _ = names
    hits = sorted((work / "RPMS").rglob(f"{name}-{version}-*.rpm"))
    if not hits:
        raise Fault("rpmbuild 没有产出 RPM", EXIT_BUILD)
    return hits[0]


def _dnf(rpm: Path) -> None:
    _need(*DNF)
    code = _spawn(["dnf", "install", "-y", str(rpm)])
    if code != 0:
        raise Fault(f"dnf 安装失败（退出 {code}）", EXIT_BUILD)


def _need(tool: str, package: str) -> None:
    if shutil.which(tool) is None:
        raise Fault(f"缺少 {tool}，请安装 {package} 包")


def _spawn(argv: list[str]) -> int:
    return subprocess.run(argv, check=False).returncode


def _quiet(argv: list[str]) -> tuple[int, str]:
    done = subprocess.run(argv, check=False, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True)
    return done.returncode, done.stdout
