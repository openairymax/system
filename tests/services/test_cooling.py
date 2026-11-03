# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 散热设备清单的单元测试
#
# 用例只围绕三件事：分类认不认得出风扇、读不出档位的设备会不会被当成正常、
# 以及清单过程有没有动过任何节点。最后一条是这份代码存在的理由——285H 上
# 写一次 cur_state 就把 ACPI 方法打坏，清单必须证明自己只读。

"""Cooling inventory tests."""

from __future__ import annotations

import unittest

from power_fixture import Tree

from airymax_power import cooling


class Collect(Tree):
    def test_missing_tree(self) -> None:
        """没有 thermal 目录时返回空清单，不伪造设备。"""
        found = cooling.collect(self.policy(thermal=str(self.base / "none")))
        self.assertEqual(found.devices, ())
        self.assertEqual(found.dump()["total"], 0)

    def test_field_shape(self) -> None:
        """按 285H 现场铺一台机器：四个风扇、十六个处理器冷却、一个压制器。"""
        for index in range(1, 5):
            self.device(index, "Fan", 1, None)
        for index in range(5, 21):
            self.device(index, "Processor", 3)
        self.device(21, "intel_powerclamp", 100)
        found = cooling.collect(self.policy())
        self.assertEqual(len(found.devices), 21)
        self.assertEqual(found.count(cooling.FAN), 4)
        self.assertEqual(found.count(cooling.CORE), 16)
        self.assertEqual(found.count(cooling.CLAMP), 1)
        self.assertEqual(found.readable, 17)
        self.assertEqual(found.engaged, 0)

    def test_order(self) -> None:
        """cooling_device2 排在 cooling_device10 之前，证据按序号呈现。"""
        self.device(10, "Processor", 3)
        self.device(2, "Fan", 1)
        found = cooling.collect(self.policy())
        self.assertEqual([device.name for device in found.devices],
                         ["cooling_device2", "cooling_device10"])

    def test_engaged(self) -> None:
        """非零档位计入用掉的散热手段；读不出的设备不算。"""
        self.device(2, "Fan", 1, "1")
        self.device(3, "Fan", 1, None)
        found = cooling.collect(self.policy())
        self.assertEqual(found.engaged, 1)
        self.assertTrue(found.fans[0].engaged)
        self.assertFalse(found.fans[1].readable)

    def test_unknown_label(self) -> None:
        """认不出的类型归 other，而不是猜成风扇。"""
        self.device(2, "ACPI Thermal", 2)
        found = cooling.collect(self.policy())
        self.assertEqual(found.devices[0].kind, cooling.OTHER)
        self.assertEqual(found.count(cooling.FAN), 0)

    def test_read_only(self) -> None:
        """清单前后 cur_state 一字不变：这份代码不写散热设备。"""
        self.device(2, "Fan", 1, "1")
        path = self.base / "thermal" / "cooling_device2" / "cur_state"
        before = path.read_text(encoding="ascii")
        cooling.collect(self.policy())
        self.assertEqual(path.read_text(encoding="ascii"), before)

    def test_dump(self) -> None:
        """JSON 里既给汇总结论，也保留每台设备的原始读回。"""
        self.device(2, "Fan", 1, "0")
        found = cooling.collect(self.policy()).dump()
        self.assertEqual(found["fans"], 1)
        self.assertEqual(found["readable"], 1)
        self.assertEqual(found["devices"], [{
            "name": "cooling_device2",
            "type": "Fan",
            "kind": "fan",
            "max": 1,
            "state": 0,
        }])


if __name__ == "__main__":
    unittest.main()