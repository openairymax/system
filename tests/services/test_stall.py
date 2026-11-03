# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 整机停顿取证的单元测试
#
# 这份代码只在一种情况下有价值：机器整机停住几分钟、恢复后什么都不记得。
# 因此用例必须证明三件事——正常间隔不报、超预算的间隔报出准确的下界、
# 证据写得进去也读得回来。时钟由用例注入，不靠真的把机器卡住三分钟。

"""Stall evidence tests."""

from __future__ import annotations

import unittest

from power_fixture import Tree

from airymax_power import stall


class Beat(Tree):
    def test_first_run(self) -> None:
        """第一次运行只落下心跳，没有前值就不能算出间隔。"""
        self.assertIsNone(stall.watch(self.policy(), now=1000.0))
        self.assertTrue((self.base / "beat").exists())

    def test_normal_gap(self) -> None:
        """60 s 的巡检间隔加定时器精度仍在预算内。"""
        policy = self.policy()
        stall.watch(policy, now=1000.0)
        self.assertIsNone(stall.watch(policy, now=1060.0))
        self.assertFalse((self.base / "pause").exists())

    def test_slack_edge(self) -> None:
        """刚好等于预算不算停顿；多一秒才算。"""
        policy = self.policy()
        stall.watch(policy, now=1000.0)
        edge = 1000.0 + stall.BUDGET
        self.assertIsNone(stall.watch(policy, now=edge))
        self.assertIsNotNone(stall.watch(policy, now=edge + stall.BUDGET + 1))

    def test_pause_value(self) -> None:
        """200 s 的间隔在 90 s 预算下报出 110 s 的停顿下界。"""
        policy = self.policy()
        stall.watch(policy, now=1000.0)
        found = stall.watch(policy, now=1200.0)
        assert found is not None
        self.assertAlmostEqual(found.gap, 200.0)
        self.assertAlmostEqual(found.over, 200.0 - stall.BUDGET)
        self.assertRegex(found.at, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_persisted(self) -> None:
        """停顿写进 /run，下一次再读回来是同一份数值。"""
        policy = self.policy()
        stall.watch(policy, now=1000.0)
        found = stall.watch(policy, now=1300.0)
        assert found is not None
        self.assertEqual(stall.recent(policy), found)

    def test_missing_record(self) -> None:
        """没有留痕时 recent 返回 None，不编造一次停顿。"""
        self.assertIsNone(stall.recent(self.policy()))

    def test_broken_record(self) -> None:
        """留痕被截断或改写时按无记录处理，不抛异常。"""
        self._write(self.base / "pause", "200.0")
        self.assertIsNone(stall.recent(self.policy()))
        self._write(self.base / "pause", "x y z")
        self.assertIsNone(stall.recent(self.policy()))

    def test_broken_beat(self) -> None:
        """心跳被改写时不把整个巡检拖垮，只当作没有前值。"""
        policy = self.policy()
        self._write(self.base / "beat", "garbage")
        self.assertIsNone(stall.watch(policy, now=1000.0))
        self.assertEqual((self.base / "beat").read_text(encoding="ascii").strip(),
                         "1000.000")

    def test_unwritable_beat(self) -> None:
        """心跳写不进去（只读 /run）时不抛异常，间隔照常计算。"""
        policy = self.policy(beat="/proc/airy-beat")
        stall.watch(policy, now=1000.0)
        self.assertIsNone(stall.watch(policy, now=1000.0))

    def test_dump(self) -> None:
        """JSON 保留一位小数，时间戳原样带出。"""
        found = stall.Pause(200.04, 110.04, "2026-11-03T12:00:00Z").dump()
        self.assertEqual(found, {
            "gap": 200.0,
            "over": 110.0,
            "at": "2026-11-03T12:00:00Z",
        })


if __name__ == "__main__":
    unittest.main()