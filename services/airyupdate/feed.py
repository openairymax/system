# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 发行通道清单校验
#
# 清单是发布流水线的确定性产出，客户端只接受白名单 schema、64 位
# 小写十六进制摘要与白名单域名。任一字段缺失、越界或类型不符即整份
# 判不可信并定位到字段，不做「尽量理解」：远端字段不得把客户端变成
# 任意地址的下载器，也不得让一份坏清单退化成一条警告。

"""AirymaxOS release channel manifest validation (V-14-03).

设计依据：docs-linux/190-distribution/04-update-mechanism.md §2.3。
校验只回答「这份清单是否可被信任」，不取网络、不比较版本、不落盘。
调用方拿到 :class:`Manifest` 即表示全部字段已逐项核对。

"""

from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import urlsplit

from .identity import parse_version
from .model import (
    CHANNELS,
    FIELDS,
    ISO_FIELDS,
    ISO_OPTIONAL,
    KERNEL_FIELDS,
    NOTES_HOSTS,
    SCHEMA_VERSION,
    Asset,
    Kernel,
    Manifest,
)

__all__ = ["Untrusted", "load_manifest"]

_DIGEST = re.compile(r"^[0-9a-f]{64}$")

_STAMP = "%Y-%m-%dT%H:%M:%SZ"

_NAME_MAX = 255


class Untrusted(ValueError):
    """清单不可信，字段名指出违约位置。"""

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field


def _bad(field: str, reason: str) -> None:
    raise Untrusted(field, reason)


def _obj(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        _bad(field, "not an object")
    return value


def _keys(
    obj: dict[str, object],
    field: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> None:
    missing = sorted(required - set(obj))
    extra = sorted(set(obj) - required - optional)
    if missing:
        _bad(field, f"missing {missing}")
    if extra:
        _bad(field, f"unexpected {extra}")


def _int(obj: dict[str, object], field: str, key: str) -> int:
    value = obj[key]
    if isinstance(value, bool) or not isinstance(value, int):
        _bad(f"{field}.{key}", "not an integer")
    return value


def _text(obj: dict[str, object], field: str, key: str) -> str:
    value = obj[key]
    if not isinstance(value, str) or not value:
        _bad(f"{field}.{key}", "not a non-empty string")
    return value


def _digest(obj: dict[str, object], field: str) -> str:
    value = _text(obj, field, "sha256")
    if not _DIGEST.match(value):
        _bad(f"{field}.sha256", "not a 64-digit lowercase digest")
    return value


def _stamp(obj: dict[str, object]) -> str:
    value = _text(obj, "manifest", "released_at")
    try:
        datetime.strptime(value, _STAMP)
    except ValueError:
        _bad("manifest.released_at", f"not {_STAMP}")
    return value


def _url(obj: dict[str, object], field: str, key: str,
         hosts: frozenset[str]) -> str:
    value = _text(obj, field, key)
    parts = urlsplit(value)
    if parts.scheme != "https":
        _bad(f"{field}.{key}", "not an https URL")
    if parts.hostname not in hosts:
        _bad(f"{field}.{key}", f"untrusted host {parts.hostname!r}")
    if parts.username is not None or parts.password is not None:
        _bad(f"{field}.{key}", "URL carries userinfo")
    return value


def _asset(obj: dict[str, object], hosts: frozenset[str]) -> Asset:
    _keys(obj, "iso", ISO_FIELDS, ISO_OPTIONAL)
    name = _text(obj, "iso", "name")
    if name in {".", ".."} or "/" in name or "\\" in name:
        _bad("iso.name", "not a plain file name")
    if len(name) > _NAME_MAX:
        _bad("iso.name", "too long")
    size = _int(obj, "iso", "size")
    if size < 0:
        _bad("iso.size", "negative")
    if "url" in obj:
        _url(obj, "iso", "url", hosts)
    return Asset(name, size, _digest(obj, "iso"))


def _kernel(obj: dict[str, object]) -> Kernel:
    _keys(obj, "kernel", KERNEL_FIELDS)
    return Kernel(_text(obj, "kernel", "nevra"), _digest(obj, "kernel"))


def load_manifest(
    raw: bytes | str,
    hosts: frozenset[str] = NOTES_HOSTS,
) -> Manifest:
    """校验并解析通道清单。

    Args:
        raw: 清单的原始字节或文本。
        hosts: 可信域名白名单，缺省取模块常量。

    Returns:
        逐字段校验通过的清单。

    Raises:
        Untrusted: 任一字段缺失、类型不符或越界。
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _bad("manifest", f"not JSON: {exc.msg}")
    top = _obj(data, "manifest")
    _keys(top, "manifest", FIELDS)
    if _int(top, "manifest", "schema_version") != SCHEMA_VERSION:
        _bad("manifest.schema_version", "unsupported schema")
    channel = _text(top, "manifest", "channel")
    if channel not in CHANNELS:
        _bad("manifest.channel", f"unknown channel {channel!r}")
    version = _text(top, "manifest", "version")
    try:
        parse_version(version)
    except ValueError:
        _bad("manifest.version", f"malformed {version!r}")
    if _int(top, "manifest", "build") < 0:
        _bad("manifest.build", "negative")
    return Manifest(
        channel=channel,
        version=version,
        build=_int(top, "manifest", "build"),
        released_at=_stamp(top),
        iso=_asset(_obj(top["iso"], "iso"), hosts),
        kernel=_kernel(_obj(top["kernel"], "kernel")),
        notes_url=_url(top, "manifest", "notes_url", hosts),
    )
