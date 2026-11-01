# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS fstab 判定回归测试
#
# 每条用例只喂一段 fstab 文本，判定必须只由文本决定。重点覆盖真实装机
# 里见过的两种坏形态：按设备名写源（扩容后改号失挂）、数据分区 noauto
# （装了但开机不挂），以及注释与短行不得被当成记录。

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airydisk import fstab  # noqa: E402

GOOD = """\
# /etc/fstab
UUID=1111-2222  /       btrfs  defaults  0 0
UUID=3333-4444  /data   xfs    defaults  0 0
"""


class ParseTest(unittest.TestCase):
    """解析只认有效记录，注释与短行不参与判定。"""

    def test_skips_noise(self) -> None:
        rows = fstab.parse("# 注释\n\nbad line\n" + GOOD)
        self.assertEqual([row.mount for row in rows], ["/", "/data"])

    def test_opts(self) -> None:
        rows = fstab.parse("UUID=a / ext4 rw,noatime 0 1\n")
        self.assertEqual(rows[0].opts, ("rw", "noatime"))

    def test_opts_absent(self) -> None:
        rows = fstab.parse("UUID=a / ext4\n")
        self.assertEqual(rows[0].opts, ())


class CheckTest(unittest.TestCase):
    """判定：ok 之外的五种形态各自给出稳定 token。"""

    def test_ok(self) -> None:
        self.assertIs(fstab.check(GOOD).state, fstab.State.OK)

    def test_ok_label(self) -> None:
        text = "LABEL=sys / ext4 defaults 0 1\nLABEL=data /data xfs defaults 0 2\n"
        self.assertIs(fstab.check(text).state, fstab.State.OK)

    def test_ok_partuuid(self) -> None:
        text = ("PARTUUID=a / ext4 defaults 0 1\n"
                "PARTUUID=b /data xfs defaults 0 2\n")
        self.assertIs(fstab.check(text).state, fstab.State.OK)

    def test_no_root(self) -> None:
        text = "UUID=b /data xfs defaults 0 2\n"
        self.assertIs(fstab.check(text).state, fstab.State.NO_ROOT)

    def test_no_data(self) -> None:
        text = "UUID=a / ext4 defaults 0 1\n"
        self.assertIs(fstab.check(text).state, fstab.State.NO_DATA)

    def test_unstable_root(self) -> None:
        text = "/dev/sda2 / ext4 defaults 0 1\nUUID=b /data xfs defaults 0 2\n"
        self.assertIs(fstab.check(text).state, fstab.State.UNSTABLE)

    def test_unstable_data(self) -> None:
        text = "UUID=a / ext4 defaults 0 1\n/dev/sdb1 /data xfs defaults 0 2\n"
        self.assertIs(fstab.check(text).state, fstab.State.UNSTABLE)

    def test_noauto(self) -> None:
        text = ("UUID=a / ext4 defaults 0 1\n"
                "UUID=b /data xfs noauto 0 2\n")
        self.assertIs(fstab.check(text).state, fstab.State.NOAUTO)

    def test_ok_property(self) -> None:
        self.assertTrue(fstab.check(GOOD).ok)
        self.assertFalse(fstab.check("").ok)


class DumpTest(unittest.TestCase):
    """JSON 形态固定为 persist 一个键，供门禁直读。"""

    def test_ok(self) -> None:
        self.assertEqual(fstab.dump(fstab.check(GOOD)), {"persist": "ok"})

    def test_failed(self) -> None:
        found = fstab.dump(fstab.check("/dev/sda2 / ext4 defaults 0 1\n"))
        self.assertEqual(found, {"persist": "no-data"})


if __name__ == "__main__":
    unittest.main()
