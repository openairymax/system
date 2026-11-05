# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 发行通道取件
#
# 只取固定地址的静态清单：地址、协议与落地域名都在本地白名单内，
# 重定向后的最终地址同样复核。远端字段不能把客户端变成任意地址的
# 下载器，也不能用超大响应把内存吃满。

"""Fetch and validate the release channel manifest (V-14-06).

设计依据：docs-linux/190-distribution/04-update-mechanism.md §2.3。
本模块只做取件与校验，不比较版本、不写状态；失败一律抛 :class:`Fetch`
或其子类 :class:`NoFeed`，调用方据异常决定退出码与文案。

"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

from .feed import Untrusted, load_manifest
from .model import FEED_URL, NOTES_HOSTS, Manifest

__all__ = ["Fetch", "NoFeed", "fetch_manifest"]

TIMEOUT = 15

MAX_BYTES = 64 * 1024


class Fetch(RuntimeError):
    """清单不可取或不可信。"""


class NoFeed(Fetch):
    """渠道尚未发布清单。

    ``releases/latest/download/<asset>`` 在远端没有任何 release、或最新
    release 未附带该资产时都回 404。两种情况下客户端都没有可比较的对象，
    这不是故障而是版本生命周期的起点：把它当失败，会让每个版本装机后的
    第一次日常检查必然报错。其余 HTTP 错误码仍按故障处理——那才是真的
    连上了却取不到东西。
    """


def _check(url: str) -> None:
    """拒绝非白名单地址；请求前先判，避免把请求发出去再后悔。"""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise Fetch(f"feed: scheme {parts.scheme!r} not allowed")
    if parts.username is not None or parts.password is not None:
        raise Fetch("feed: credentials in URL not allowed")
    if parts.hostname not in NOTES_HOSTS:
        raise Fetch(f"feed: host {parts.hostname!r} not allowed")


def _read(resp, limit: int) -> bytes:
    """多读一字节即可判定越界，不必把整份响应收进内存。"""
    raw = resp.read(limit + 1)
    if len(raw) > limit:
        raise Fetch(f"feed: body exceeds {limit} bytes")
    return raw


def fetch_manifest(url: str = FEED_URL, timeout: float = TIMEOUT,
                   limit: int = MAX_BYTES) -> Manifest:
    """取回并校验发行通道清单。

    Args:
        url: 清单地址，缺省取发行通道固定地址。
        timeout: 连接与读取超时（秒）。
        limit: 响应体上限（字节）。

    Returns:
        校验通过的清单。

    Raises:
        NoFeed: 远端尚未发布清单（HTTP 404）。
        Fetch: 地址越界、其它网络失败、响应过大或清单不可信。
    """
    _check(url)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            _check(resp.geturl())
            raw = _read(resp, limit)
    except Fetch:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NoFeed(f"feed: not published yet (HTTP 404): {url}") from exc
        raise Fetch(f"feed: {exc}") from exc
    except (OSError, ValueError) as exc:
        raise Fetch(f"feed: {exc}") from exc
    try:
        return load_manifest(raw)
    except Untrusted as exc:
        raise Fetch(f"feed.{exc.field or 'body'}: {exc}") from exc
