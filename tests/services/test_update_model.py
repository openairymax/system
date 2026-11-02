# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 升级通道契约测试
#
# 校验 model 的协议面：退出码三态可区分、通道白名单只有 stable、
# 取件地址与发行说明域名同源、清单字段白名单覆盖数据类全部字段。
# 取件与判定行为不在本文件覆盖，由后续实现层的测试承担。

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyupdate import model  # noqa: E402


class ExitTest(unittest.TestCase):
    """退出码是脚本契约：并行与串行调用方都按它分流。"""

    def test_values(self) -> None:
        self.assertEqual(
            (model.EXIT_OK, model.EXIT_FAIL, model.EXIT_NEW,
             model.EXIT_REINSTALL),
            (0, 1, 10, 20),
        )

    def test_distinct(self) -> None:
        codes = {model.EXIT_OK, model.EXIT_FAIL, model.EXIT_NEW,
                 model.EXIT_REINSTALL}
        self.assertEqual(len(codes), 4)


class FeedTest(unittest.TestCase):
    """清单地址与说明域名共用一份白名单，不各自维护。"""

    def test_feed_https(self) -> None:
        parts = urlsplit(model.FEED_URL)
        self.assertEqual(parts.scheme, "https")
        self.assertEqual(parts.netloc, "github.com")
        self.assertTrue(parts.path.endswith("/channel.json"))

    def test_feed_host_trusted(self) -> None:
        self.assertIn(urlsplit(model.FEED_URL).netloc, model.NOTES_HOSTS)

    def test_channel_whitelist(self) -> None:
        self.assertEqual(model.CHANNELS, {"stable"})
        self.assertEqual(model.SCHEMA_VERSION, 1)


class ShapeTest(unittest.TestCase):
    """字段白名单必须覆盖数据类，缺一字段即整份不可信。"""

    def test_manifest_fields(self) -> None:
        self.assertEqual(
            model.FIELDS,
            {
                "schema_version", "channel", "version", "build",
                "released_at", "iso", "kernel", "notes_url",
            },
        )

    def test_asset_fields(self) -> None:
        self.assertEqual(model.ISO_FIELDS, {"name", "size", "sha256"})
        self.assertEqual(model.ISO_OPTIONAL, {"url"})
        self.assertEqual(model.KERNEL_FIELDS, {"nevra", "sha256"})

    def test_manifest_immutable(self) -> None:
        iso = model.Asset("airymaxos-0.0.4.iso", 1, "0" * 64)
        kernel = model.Kernel("airymaxos-kernel-0.0.4-3.x86_64", "0" * 64)
        item = model.Manifest(
            "stable", "0.0.4", 3, "2026-11-20T00:00:00Z",
            iso, kernel, "https://github.com/openairymax/agent-linux",
        )
        with self.assertRaises(AttributeError):
            item.build = 4  # type: ignore[misc]


class StateTest(unittest.TestCase):
    """状态文件只由 check 写，路径必须是绝对目录下的单文件。"""

    def test_state_path(self) -> None:
        self.assertTrue(Path(model.STATE_DIR).is_absolute())
        self.assertEqual(model.STATE_FILE, "state.json")
        self.assertNotIn("/", model.STATE_FILE)


if __name__ == "__main__":
    unittest.main()
