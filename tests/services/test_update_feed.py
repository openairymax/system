# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 发行清单校验测试
#
# 覆盖 §2.3 的四类拒绝：缺字段、坏摘要、越界域名、未知 schema，
# 外加类型不符与多余字段。每个用例断言错误定位到字段，不只断言
# 「抛了异常」——否则无法说明客户端究竟为何拒绝这份清单。

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyupdate import feed  # noqa: E402

DIGEST = "a1" * 32


def sample() -> dict:
    """构造一份完全合法的清单，用例按需破坏单个字段。"""
    return {
        "schema_version": 1,
        "channel": "stable",
        "version": "0.0.4",
        "build": 3,
        "released_at": "2026-11-20T00:00:00Z",
        "iso": {
            "name": "airymaxos-0.0.4.iso",
            "size": 2147483648,
            "sha256": DIGEST,
        },
        "kernel": {
            "nevra": "airymaxos-kernel-0.0.4-3.x86_64",
            "sha256": DIGEST,
        },
        "notes_url": (
            "https://github.com/openairymax/agent-linux/releases/tag/v0.0.4"
        ),
    }


class AcceptTest(unittest.TestCase):
    """合法清单逐字段落成数据类。"""

    def test_fields(self) -> None:
        item = feed.load_manifest(json.dumps(sample()))
        self.assertEqual(item.channel, "stable")
        self.assertEqual(item.version, "0.0.4")
        self.assertEqual(item.build, 3)
        self.assertEqual(item.iso.name, "airymaxos-0.0.4.iso")
        self.assertEqual(item.iso.size, 2147483648)
        self.assertEqual(item.iso.sha256, DIGEST)
        self.assertEqual(item.kernel.nevra, "airymaxos-kernel-0.0.4-3.x86_64")
        self.assertTrue(item.notes_url.startswith("https://github.com/"))

    def test_iso_url_optional(self) -> None:
        data = sample()
        data["iso"]["url"] = "https://github.com/openairymax/agent-linux/x.iso"
        self.assertEqual(
            feed.load_manifest(json.dumps(data)).iso.sha256,
            DIGEST,
        )

    def test_bytes_input(self) -> None:
        self.assertEqual(
            feed.load_manifest(json.dumps(sample()).encode()).build,
            3,
        )


class RejectTest(unittest.TestCase):
    """任一违约即整份不可信，且错误定位到字段。"""

    def reject(self, data: object, field: str) -> None:
        with self.assertRaises(feed.Untrusted) as ctx:
            feed.load_manifest(json.dumps(data))
        self.assertEqual(ctx.exception.field, field)

    def test_missing_field(self) -> None:
        data = sample()
        del data["build"]
        self.reject(data, "manifest")

    def test_extra_field(self) -> None:
        data = sample()
        data["mirror"] = "https://example.com"
        self.reject(data, "manifest")

    def test_unknown_schema(self) -> None:
        for value in (2, 0, "1", True):
            data = sample()
            data["schema_version"] = value
            with self.subTest(value=value):
                self.reject(data, "manifest.schema_version")

    def test_unknown_channel(self) -> None:
        data = sample()
        data["channel"] = "beta"
        self.reject(data, "manifest.channel")

    def test_bad_version(self) -> None:
        for value in ("0.0", "0.0.4.1", "v0.0.4"):
            data = sample()
            data["version"] = value
            with self.subTest(value=value):
                self.reject(data, "manifest.version")

    def test_bad_build(self) -> None:
        for value in (-1, "3", 3.0, True):
            data = sample()
            data["build"] = value
            with self.subTest(value=value):
                self.reject(data, "manifest.build")

    def test_bad_stamp(self) -> None:
        for value in ("2026-11-20", "2026-11-20 00:00:00", "2026-13-01T00:00:00Z"):
            data = sample()
            data["released_at"] = value
            with self.subTest(value=value):
                self.reject(data, "manifest.released_at")

    def test_bad_digest(self) -> None:
        for value in ("a" * 63, "a" * 65, "A" * 64, "g" * 64, ""):
            data = sample()
            data["iso"]["sha256"] = value
            with self.subTest(value=value[:8]):
                self.reject(data, "iso.sha256")

    def test_iso_missing_digest(self) -> None:
        data = sample()
        del data["iso"]["sha256"]
        self.reject(data, "iso")

    def test_iso_extra_field(self) -> None:
        data = sample()
        data["iso"]["chunk_size"] = 1
        self.reject(data, "iso")

    def test_iso_bad_name(self) -> None:
        for value in ("../x.iso", "a/b.iso", "..", ""):
            data = sample()
            data["iso"]["name"] = value
            with self.subTest(value=value):
                self.reject(data, "iso.name")

    def test_iso_negative_size(self) -> None:
        data = sample()
        data["iso"]["size"] = -1
        self.reject(data, "iso.size")

    def test_iso_url_host(self) -> None:
        for value in (
            "http://github.com/x.iso",
            "https://example.com/x.iso",
            "https://github.com@example.com/x.iso",
        ):
            data = sample()
            data["iso"]["url"] = value
            with self.subTest(value=value):
                self.reject(data, "iso.url")

    def test_kernel_shape(self) -> None:
        data = sample()
        data["kernel"]["sha256"] = DIGEST
        data["kernel"]["size"] = 1
        self.reject(data, "kernel")

    def test_notes_host(self) -> None:
        for value in (
            "https://example.com/notes",
            "https://github.com.evil.example/notes",
            "http://github.com/notes",
        ):
            data = sample()
            data["notes_url"] = value
            with self.subTest(value=value):
                self.reject(data, "manifest.notes_url")

    def test_not_object(self) -> None:
        for value in ([], "manifest", 1):
            with self.subTest(value=value):
                self.reject(value, "manifest")

    def test_not_json(self) -> None:
        with self.assertRaises(feed.Untrusted) as ctx:
            feed.load_manifest("{")
        self.assertEqual(ctx.exception.field, "manifest")


class TrustTest(unittest.TestCase):
    """白名单可由调用方收紧，不写死在解析里。"""

    def test_hosts_override(self) -> None:
        data = sample()
        with self.assertRaises(feed.Untrusted):
            feed.load_manifest(json.dumps(data), frozenset({"example.com"}))
        data["notes_url"] = "https://example.com/notes"
        item = feed.load_manifest(
            json.dumps(copy.deepcopy(data)),
            frozenset({"example.com"}),
        )
        self.assertEqual(item.notes_url, "https://example.com/notes")


if __name__ == "__main__":
    unittest.main()
