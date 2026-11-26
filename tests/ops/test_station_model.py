# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""模型与权限排序表的单元测试。"""

import unittest

from devstation import model


class PanelTest(unittest.TestCase):
    def test_eight_panels(self):
        self.assertEqual(len(model.PANELS), 8)
        self.assertEqual(len({item.value for item in model.PANELS}), 8)

    def test_hotkeys_follow_order(self):
        for index, item in enumerate(model.PANELS, 1):
            self.assertEqual(model.HOTKEY[str(index)], item)
            self.assertTrue(model.TITLE[item])

    def test_parse_panel(self):
        self.assertEqual(model.parse_panel("logs"), model.Panel.LOGS)
        with self.assertRaises(model.BadInput) as caught:
            model.parse_panel("nope")
        self.assertEqual(caught.exception.code, 2)


class RankTest(unittest.TestCase):
    def test_strict_order(self):
        ranks = [model.RANK[name] for name in ("view", "operate", "admin")]
        self.assertEqual(ranks, [0, 1, 2])
        self.assertEqual(model.rank_of("admin"), 2)
        with self.assertRaises(model.BadInput):
            model.rank_of("root")

    def test_need_perm_matrix(self):
        for level, action, ok in (
            ("view", "agent.start", False),
            ("operate", "agent.start", True),
            ("operate", "memory.erase", False),
            ("admin", "memory.erase", True),
        ):
            if ok:
                model.need_perm(level, action)
            else:
                with self.assertRaises(model.BadPerm) as caught:
                    model.need_perm(level, action)
                self.assertEqual(caught.exception.code, 4)

    def test_unknown_action(self):
        with self.assertRaises(model.BadInput):
            model.need_perm("admin", "agent.teleport")


class ErrorCodeTest(unittest.TestCase):
    def test_codes(self):
        self.assertEqual(model.BadInput("x").code, 2)
        self.assertEqual(model.BadState("x").code, 3)
        self.assertEqual(model.BadUnsupported("x").code, 3)
        self.assertEqual(model.BadConfig("x").code, 3)
        self.assertEqual(model.BadPerm("x").code, 4)

    def test_need(self):
        model.need(True, "不会抛")
        with self.assertRaises(model.BadState):
            model.need(False, "缺源", model.BadState)


class SnapshotTest(unittest.TestCase):
    def test_json_shape(self):
        sec = model.Section("monitor", rows=[{"daemon": "x"}])
        snap = model.Snapshot(stamp=model.now_ns(), node="n1",
                              level="view", sections={"monitor": sec})
        doc = snap.to_json()
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(doc["node"], "n1")
        self.assertEqual(doc["sections"]["monitor"]["rows"], [{"daemon": "x"}])
        self.assertTrue(doc["stamp"].endswith("Z"))

    def test_missing_section(self):
        snap = model.Snapshot(stamp=1, node="n", level="view", sections={})
        sec = snap.section("logs")
        self.assertFalse(sec.ready)
        self.assertEqual(sec.status, "unavailable")
        self.assertTrue(sec.reason)

    def test_ready(self):
        self.assertTrue(model.Section("x").ready)
        self.assertFalse(model.Section("x", status="unavailable").ready)


if __name__ == "__main__":
    unittest.main()
