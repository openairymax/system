# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 升级通道数据契约
#
# 版本身份、发行清单与客户端退出码的类型声明。0.0.x 的「更新」只有
# 版本发现、基底勘误与跨版本重装三项，接口在此钉住：字段名、白名单
# 与退出码改变即视为协议变更，0.1.x 的原子通道必须沿用同一套。

"""AirymaxOS update channel model types (V-14-01).

设计依据：docs-linux/190-distribution/04-update-mechanism.md §2.2~§2.5。
本模块只声明契约，不取网络、不读文件、不比较版本；取值与判定分别由
身份层、清单层与状态层承担，避免把「格式正确」误当成「内容可信」。

"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CHANNELS",
    "CHANNEL_STABLE",
    "EXIT_FAIL",
    "EXIT_NEW",
    "EXIT_OK",
    "EXIT_REINSTALL",
    "FEED_URL",
    "FIELDS",
    "ISO_FIELDS",
    "ISO_OPTIONAL",
    "KERNEL_FIELDS",
    "NOTES_HOSTS",
    "SCHEMA_VERSION",
    "STATE_DIR",
    "STATE_FILE",
    "Asset",
    "Identity",
    "Kernel",
    "Manifest",
]

SCHEMA_VERSION = 1

CHANNEL_STABLE = "stable"

CHANNELS = frozenset({CHANNEL_STABLE})

FEED_URL = (
    "https://github.com/openairymax/agent-linux"
    "/releases/latest/download/channel.json"
)

NOTES_HOSTS = frozenset({"github.com"})

STATE_DIR = "/var/lib/airy-update"

STATE_FILE = "state.json"

EXIT_OK = 0

EXIT_FAIL = 1

EXIT_NEW = 10

EXIT_REINSTALL = 20

FIELDS = frozenset({
    "schema_version",
    "channel",
    "version",
    "build",
    "released_at",
    "iso",
    "kernel",
    "notes_url",
})

ISO_FIELDS = frozenset({"name", "size", "sha256"})

ISO_OPTIONAL = frozenset({"url"})

KERNEL_FIELDS = frozenset({"nevra", "sha256"})


@dataclass(frozen=True)
class Identity:
    """本地版本身份三元组。

    Attributes:
        version: ``os-release`` 的 ``VERSION_ID``，形如 ``0.0.4``。
        build: ``airymaxos-release`` 的 Release 整数段。
        kernel: ``uname -r`` 的逐字输出。
    """

    version: str
    build: int
    kernel: str


@dataclass(frozen=True)
class Asset:
    """可下载资产的最小描述。

    Attributes:
        name: 资产文件名，不得含路径分隔符。
        size: 字节数。
        sha256: 64 位十六进制摘要。
    """

    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Kernel:
    """内核 RPM 的标识与摘要。

    Attributes:
        nevra: ``name-version-release.arch`` 形态。
        sha256: 64 位十六进制摘要。
    """

    nevra: str
    sha256: str


@dataclass(frozen=True)
class Manifest:
    """发行通道清单。

    Attributes:
        channel: 通道名，0.0.x 只有 ``stable``。
        version: 主版本三段数值。
        build: 构建号，同版本内单调递增。
        released_at: UTC 时间戳，形如 ``2026-11-20T00:00:00Z``。
        iso: 安装介质资产。
        kernel: 内核 RPM 资产。
        notes_url: 发行说明地址，域名须在白名单内。
    """

    channel: str
    version: str
    build: int
    released_at: str
    iso: Asset
    kernel: Kernel
    notes_url: str
