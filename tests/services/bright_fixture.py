# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 背光测试共用的临时 sysfs 树
#
# 判定层与命令行层需要同一棵「一块 firmware 屏、一块 raw 面板、一条键盘
# 背光、一条与背光无关的指示 led」的临时树。默认形状取 285H 现场：屏幕
# 上限 1000、当前 400（40%），键盘上限 3、当前 2。用例只改自己关心的那
# 一处，目录拼装与路径选项收在这里，两个测试文件不再各抄一份。

"""Shared temporary sysfs tree for the backlight tests."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airybright import device, policy  # noqa: E402


class Tree(unittest.TestCase):
    """一棵临时背光树：firmware 屏 + raw 面板 + 键盘背光 + 无关 led。"""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name)
        self.backlight = self.base / "backlight"
        self.leds = self.base / "leds"
        self.screen("intel_backlight", 1000, 400, "firmware")
        self.screen("panel0", 937, 468, "raw")
        self.kbd("platform::kbd_backlight", 3, 2)
        self.led("input0::capslock", 1, 0)

    def policy(self, **over: object) -> policy.Policy:
        fields = {
            "backlight": str(self.backlight),
            "leds": str(self.leds),
            "floor": policy.SCREEN_FLOOR,
        }
        fields.update(over)
        return policy.Policy(**fields)

    def screen(self, name: str, maximum: int, raw: int | None, kind: str,
               actual: int | None = None) -> None:
        """铺一块屏幕面板；raw=None 表示 brightness 节点读不出来。"""
        path = self.backlight / name
        path.mkdir(parents=True, exist_ok=True)
        self._write(path / "max_brightness", maximum)
        self._write(path / "type", kind)
        if raw is not None:
            self._write(path / "brightness", raw)
        if actual is not None:
            self._write(path / "actual_brightness", actual)

    def kbd(self, name: str, maximum: int, raw: int | None) -> None:
        path = self.leds / name
        path.mkdir(parents=True, exist_ok=True)
        self._write(path / "max_brightness", maximum)
        if raw is not None:
            self._write(path / "brightness", raw)

    def led(self, name: str, maximum: int, raw: int) -> None:
        """与背光无关的指示 led：没有 kbd_backlight 子串，必须被忽略。"""
        self.kbd(name, maximum, raw)

    def drop(self, folder: Path, name: str) -> None:
        shutil.rmtree(folder / name, ignore_errors=True)

    def wipe(self) -> None:
        """两棵子树都清空，模拟 KMS 尚未接管背光的开机早期。"""
        shutil.rmtree(self.backlight, ignore_errors=True)
        shutil.rmtree(self.leds, ignore_errors=True)
        self.backlight.mkdir(parents=True, exist_ok=True)
        self.leds.mkdir(parents=True, exist_ok=True)

    def devs(self, target: policy.Target) -> tuple[device.Device, ...]:
        """按当前树扫描一次；用例只关心名字与顺序。"""
        return device.scan(self.policy(), target)

    def level(self, folder: Path, name: str) -> int:
        """读回一次 brightness，断言写入真的落在文件里。"""
        text = (folder / name / "brightness").read_text(encoding="ascii")
        return int(text.strip())

    def args(self, cmd: str, *rest: str) -> list[str]:
        """把子命令接到本机临时路径上。"""
        return [
            cmd,
            "--backlight", str(self.backlight),
            "--leds", str(self.leds),
            *rest,
        ]

    @staticmethod
    def _write(path: Path, value: object) -> None:
        path.write_text(f"{value}\n", encoding="ascii")
