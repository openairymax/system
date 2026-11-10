# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 发行通道取件测试
#
# 取件层的职责只有两件：只去白名单地址、只接受可信清单。两个方向
# 都要有失败用例，否则「能下载」会被误当成「下载到了正确的东西」。

from __future__ import annotations

import json
import socket
import ssl
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyupdate import net  # noqa: E402


def sample() -> dict:
    return {
        "schema_version": 1,
        "channel": "stable",
        "version": "0.0.4",
        "build": 3,
        "released_at": "2026-11-20T00:00:00Z",
        "iso": {"name": "airymaxos-0.0.4.iso", "size": 1024,
                "sha256": "a" * 64},
        "kernel": {"nevra": "airymaxos-kernel-0.0.4-3.x86_64",
                   "sha256": "b" * 64},
        "notes_url": "https://github.com/openairymax/agent-linux/"
                     "releases/tag/v0.0.4",
    }


class Response:
    def __init__(self, body: bytes, url: str = net.FEED_URL) -> None:
        self.body = body
        self.url = url
        self.asked = 0

    def read(self, size: int) -> bytes:
        self.asked = size
        return self.body[:size]

    def geturl(self) -> str:
        return self.url

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *exc) -> None:
        return None


class Stalled(Response):
    """连接已建立而读取卡死：链路异常，不是清单内容的问题。"""

    def read(self, size: int) -> bytes:
        raise TimeoutError("read timed out")


class AcceptTest(unittest.TestCase):
    def test_returns_manifest(self) -> None:
        body = json.dumps(sample()).encode()
        with mock.patch.object(net.urllib.request, "urlopen",
                               return_value=Response(body)):
            manifest = net.fetch_manifest()
        self.assertEqual(manifest.version, "0.0.4")
        self.assertEqual(manifest.build, 3)

    def test_bounds_read(self) -> None:
        body = json.dumps(sample()).encode()
        resp = Response(body)
        with mock.patch.object(net.urllib.request, "urlopen",
                               return_value=resp):
            net.fetch_manifest()
        self.assertEqual(resp.asked, net.MAX_BYTES + 1)

    def test_timeout_passed(self) -> None:
        body = json.dumps(sample()).encode()
        with mock.patch.object(net.urllib.request, "urlopen",
                               return_value=Response(body)) as open_:
            net.fetch_manifest(timeout=3)
        self.assertEqual(open_.call_args.kwargs["timeout"], 3)


class AddressTest(unittest.TestCase):
    def assert_refused(self, url: str) -> None:
        with mock.patch.object(net.urllib.request, "urlopen") as open_:
            with self.assertRaises(net.Fetch):
                net.fetch_manifest(url)
        open_.assert_not_called()

    def test_http(self) -> None:
        self.assert_refused("http://github.com/openairymax/channel.json")

    def test_foreign_host(self) -> None:
        self.assert_refused("https://example.com/channel.json")

    def test_credentials(self) -> None:
        self.assert_refused(
            "https://user:pass@github.com/openairymax/channel.json")

    def test_redirect_elsewhere(self) -> None:
        body = json.dumps(sample()).encode()
        resp = Response(body, url="https://example.com/channel.json")
        with mock.patch.object(net.urllib.request, "urlopen",
                               return_value=resp):
            with self.assertRaises(net.Fetch):
                net.fetch_manifest()


class FailureTest(unittest.TestCase):
    def test_network_error(self) -> None:
        with mock.patch.object(net.urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("offline")):
            with self.assertRaises(net.Fetch):
                net.fetch_manifest()

    def test_missing_feed(self) -> None:
        """404 是「远端尚未发版」，要能与真正的故障区分开。"""
        err = urllib.error.HTTPError(net.FEED_URL, 404, "Not Found", {}, None)
        with mock.patch.object(net.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(net.NoFeed):
                net.fetch_manifest()

    def test_server_error_is_not_missing_feed(self) -> None:
        """连上了却取不到东西才算故障：5xx 不能被当成「尚未发版」。"""
        err = urllib.error.HTTPError(net.FEED_URL, 503, "busy", {}, None)
        with mock.patch.object(net.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(net.Fetch) as caught:
                net.fetch_manifest()
        self.assertNotIsInstance(caught.exception, net.NoFeed)

    def test_oversized(self) -> None:
        with mock.patch.object(net.urllib.request, "urlopen",
                               return_value=Response(b"x" * (net.MAX_BYTES + 1))):
            with self.assertRaises(net.Fetch):
                net.fetch_manifest()

    def test_untrusted(self) -> None:
        body = json.dumps(dict(sample(), channel="beta")).encode()
        with mock.patch.object(net.urllib.request, "urlopen",
                               return_value=Response(body)):
            with self.assertRaises(net.Fetch) as caught:
                net.fetch_manifest()
        self.assertIn("channel", str(caught.exception))

    def test_not_json(self) -> None:
        with mock.patch.object(net.urllib.request, "urlopen",
                               return_value=Response(b"{")):
            with self.assertRaises(net.Fetch):
                net.fetch_manifest()


class LinkTest(unittest.TestCase):
    """没走到对端与走到对端拿到坏东西，必须落在两个异常上。"""

    def fetch(self, error: Exception) -> net.Fetch:
        with mock.patch.object(net.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(net.Fetch) as caught:
                net.fetch_manifest()
        return caught.exception

    def test_dns_failure(self) -> None:
        err = urllib.error.URLError(socket.gaierror(-2, "Name not known"))
        self.assertIsInstance(self.fetch(err), net.Unreachable)

    def test_connect_timeout(self) -> None:
        err = urllib.error.URLError(TimeoutError("timed out"))
        self.assertIsInstance(self.fetch(err), net.Unreachable)

    def test_refused(self) -> None:
        err = urllib.error.URLError(ConnectionRefusedError(111, "refused"))
        self.assertIsInstance(self.fetch(err), net.Unreachable)

    def test_read_timeout(self) -> None:
        with mock.patch.object(net.urllib.request, "urlopen",
                               return_value=Stalled(b"")):
            with self.assertRaises(net.Unreachable):
                net.fetch_manifest()

    def test_tls_failure_is_not_unreachable(self) -> None:
        """链路已建立而对端身份不可信属安全边界，不能降级成环境状态。"""
        err = urllib.error.URLError(ssl.SSLError("bad certificate"))
        caught = self.fetch(err)
        self.assertNotIsInstance(caught, net.Unreachable)

    def test_unrecognized_reason_stays_fetch(self) -> None:
        self.assertNotIsInstance(self.fetch(urllib.error.URLError("offline")),
                                 net.Unreachable)


if __name__ == "__main__":
    unittest.main()
