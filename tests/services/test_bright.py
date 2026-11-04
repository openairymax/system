# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 背光判定层回归测试
#
# 覆盖三件事：百分比与刻度的换算边界、主设备排序、以及「写—回读」的
# 四种收场（落地、部分落地、写不通、没有设备）。回读误差按一格以内算
# 落地，因此 3 格刻度的键盘写 99% 也应当判为成功。

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from airybright import device, level, plan  # noqa: E402
from airybright.policy import Target  # noqa: E402
from airybright.report import State  # noqa: E402
from bright_fixture import Tree  # noqa: E402


class LevelTest(unittest.TestCase):
    """换算只在边界上出错，用例都取边界。"""

    def test_floor(self) -> None:
        self.assertEqual(level.clamp(-5, 1), 1)
        self.assertEqual(level.clamp(150, 1), 100)
        self.assertEqual(level.floor_raw(1000, 1), 10)
        self.assertEqual(level.floor_raw(3, 1), 1)
        self.assertEqual(level.floor_raw(1000, 0), 0)

    def test_raw(self) -> None:
        self.assertEqual(level.raw_of(50, 1000, 1), 500)
        self.assertEqual(level.raw_of(0, 1000, 1), 10)
        self.assertEqual(level.raw_of(0, 1000, 0), 0)
        self.assertEqual(level.raw_of(100, 1000, 1), 1000)
        self.assertEqual(level.raw_of(1, 3, 0), 0)

    def test_pct(self) -> None:
        self.assertEqual(level.pct_of(500, 1000), 50)
        self.assertEqual(level.pct_of(10, 1000), 1)
        self.assertEqual(level.pct_of(2, 3), 67)
        self.assertEqual(level.pct_of(3, 3), 100)
        self.assertEqual(level.pct_of(7, 0), 0)

    def test_shift(self) -> None:
        self.assertEqual(level.shift(40, 10, 1), 50)
        self.assertEqual(level.shift(95, 10, 1), 100)
        self.assertEqual(level.shift(3, -10, 1), 1)
        self.assertEqual(level.shift(0, 1, 0), 1)


class ScanTest(Tree):
    """发现与排序：类型优先，其次上限，最后名字。"""

    def test_order(self) -> None:
        names = [dev.name for dev in self.devs(Target.SCREEN)]
        self.assertEqual(names, ["intel_backlight", "panel0"])

    def test_primary(self) -> None:
        for target, want in ((Target.SCREEN, "intel_backlight"),
                             (Target.KEYBOARD, "platform::kbd_backlight")):
            self.assertEqual(device.primary(self.devs(target)).name, want)
        self.assertIsNone(device.primary(()))

    def test_pick(self) -> None:
        self.assertEqual(len(device.pick(self.policy(), Target.SCREEN)), 1)
        self.assertEqual(len(device.pick(self.policy(), Target.KEYBOARD)), 1)
        self.assertEqual(len(device.pick(self.policy(), Target.ALL)), 2)

    def test_no_cap(self) -> None:
        """没有 max_brightness 的节点当作不存在，避免后续全靠猜刻度。"""
        self.screen("ghost", 0, 1, "raw")
        names = [dev.name for dev in self.devs(Target.SCREEN)]
        self.assertNotIn("ghost", names)

    def test_kbd_filter(self) -> None:
        """与背光无关的 led 不参与，即使它有 max_brightness。"""
        names = [dev.name for dev in self.devs(Target.KEYBOARD)]
        self.assertEqual(names, ["platform::kbd_backlight"])

    def test_read_int(self) -> None:
        path = self.base / "probe"
        path.write_text("-1\n", encoding="ascii")
        self.assertIsNone(device.read_int(path))
        path.write_text("400\n", encoding="ascii")
        self.assertEqual(device.read_int(path), 400)
        self.assertIsNone(device.read_int(self.base / "absent"))


class ReadTest(Tree):
    """只读评估：百分比现算，缺席的一边也留一行。"""

    def test_ok(self) -> None:
        found = plan.read(self.policy(), Target.ALL)
        self.assertIs(found.state, State.OK)
        self.assertEqual(found.present, 2)
        self.assertEqual([row.pct for row in found.rows], [40, 67])

    def test_actual(self) -> None:
        """内核回读值优先：它才是屏幕实际亮度。"""
        self.screen("intel_backlight", 1000, 400, "firmware", actual=250)
        found = plan.read(self.policy(), Target.SCREEN)
        self.assertEqual(found.rows[0].pct, 25)

    def test_absent(self) -> None:
        self.wipe()
        found = plan.read(self.policy(), Target.ALL)
        self.assertIs(found.state, State.ABSENT)
        self.assertEqual(found.present, 0)
        self.assertEqual(found.dump()["state"], "absent")

    def test_unreadable(self) -> None:
        (self.backlight / "intel_backlight" / "brightness").unlink()
        found = plan.read(self.policy(), Target.SCREEN)
        self.assertIs(found.state, State.FAILED)

    def test_dump(self) -> None:
        data = plan.read(self.policy(), Target.SCREEN).dump()
        self.assertEqual(sorted(data), ["devices", "floor", "landed", "mark",
                                        "mode", "present", "state", "target"])
        self.assertEqual(sorted(data["devices"][0]),
                         ["error", "kind", "landed", "max", "name", "pct",
                          "present", "raw", "type", "want"])


class WriteTest(Tree):
    """写入必须回读；落地、部分、失败三种收场各有其位。"""

    def test_screen(self) -> None:
        found = plan.apply(self.policy(), Target.SCREEN, 50)
        self.assertIs(found.state, State.OK)
        self.assertEqual(self.level(self.backlight, "intel_backlight"), 500)

    def test_floor(self) -> None:
        """默认下限 1%：写 0 落到最低一格，不黑屏。"""
        found = plan.apply(self.policy(), Target.SCREEN, 0)
        self.assertIs(found.state, State.OK)
        self.assertEqual(self.level(self.backlight, "intel_backlight"), 10)

    def test_open_floor(self) -> None:
        """显式 --floor 0 才允许真的关屏。"""
        plan.apply(self.policy(floor=0), Target.SCREEN, 0)
        self.assertEqual(self.level(self.backlight, "intel_backlight"), 0)

    def test_kbd_zero(self) -> None:
        """键盘不受屏幕下限保护：关掉键盘背光是合法用法。"""
        found = plan.apply(self.policy(), Target.KEYBOARD, 0)
        self.assertIs(found.state, State.OK)
        self.assertEqual(self.level(self.leds, "platform::kbd_backlight"), 0)

    def test_partial(self) -> None:
        """键盘缺席时屏幕照样落地，整体判部分成功而不是失败。"""
        self.drop(self.leds, "platform::kbd_backlight")
        found = plan.apply(self.policy(), Target.ALL, 50)
        self.assertIs(found.state, State.PARTIAL)
        self.assertEqual(found.present, 1)
        self.assertEqual([row.error for row in found.rows][1], "no-device")

    def test_step(self) -> None:
        found = plan.step(self.policy(), Target.SCREEN, 10)
        self.assertIs(found.state, State.OK)
        self.assertEqual(self.level(self.backlight, "intel_backlight"), 500)

    def test_one_grid(self) -> None:
        """3 格刻度的键盘写 99% 落到满格，回读差一格仍判落地。"""
        found = plan.apply(self.policy(), Target.KEYBOARD, 99)
        self.assertIs(found.state, State.OK)
        self.assertEqual(self.level(self.leds, "platform::kbd_backlight"), 3)

    def test_gone(self) -> None:
        """brightness 节点消失：不写不猜，按 gone 报出。"""
        (self.backlight / "intel_backlight" / "brightness").unlink()
        found = plan.apply(self.policy(), Target.SCREEN, 50)
        self.assertIs(found.state, State.FAILED)
        self.assertEqual(found.rows[0].error, "gone")

    def test_no_reading(self) -> None:
        """没有当前读数就不做相对调节，否则基准是凭空的。"""
        (self.leds / "platform::kbd_backlight" / "brightness").unlink()
        found = plan.step(self.policy(), Target.KEYBOARD, 1)
        self.assertEqual(found.rows[0].error, "no-reading")

    def test_write_fail(self) -> None:
        path = self.backlight / "intel_backlight" / "brightness"
        path.unlink()
        path.mkdir()
        found = plan.apply(self.policy(), Target.SCREEN, 50)
        self.assertIs(found.state, State.FAILED)
        self.assertEqual(found.rows[0].error, "write-failed")

    def test_read_only(self) -> None:
        with mock.patch.object(device.os, "access", return_value=False):
            found = plan.apply(self.policy(), Target.SCREEN, 50)
        self.assertEqual(found.rows[0].error, "read-only")

    def test_readback(self) -> None:
        """写进去了但回读不符：按 readback 报出，不能算成功。"""
        dev = device.primary(device.scan(self.policy(), Target.SCREEN))
        dev.node.write_text("500\n", encoding="ascii")
        self.assertTrue(plan._check(dev, 500, 50).landed)
        with mock.patch.object(device, "read_int", return_value=2):
            row = plan._check(dev, 500, 50)
        self.assertFalse(row.landed)
        self.assertEqual(row.error, "readback")

    def test_absent(self) -> None:
        self.wipe()
        found = plan.apply(self.policy(), Target.ALL, 50)
        self.assertIs(found.state, State.ABSENT)


if __name__ == "__main__":
    unittest.main()
