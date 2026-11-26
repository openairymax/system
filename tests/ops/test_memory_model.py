# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 记忆模型测试
#
# 覆盖数据块字节布局的往返与损坏拒绝、标识与层级校验、时间与窗口工具，
# 以及可预期失败到退出码 2/3/4 的映射。

from __future__ import annotations

import datetime
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops"))
sys.path.insert(0, str(ROOT / "services"))

from memory import model  # noqa: E402
from memory.model import (  # noqa: E402
    BadConfig,
    BadInput,
    BadPerm,
    BadStore,
    BadUnsupported,
    Item,
    Level,
    Method,
    Record,
    block_name,
    check_block,
    check_id,
    check_item,
    decode_block,
    encode_block,
    exit_code,
    in_window,
    iso,
    parse_iso,
    parse_items,
    parse_level,
    parse_method,
    unsupported,
)

STAMP = parse_iso("2026-03-04T05:06:07.123456789Z")


class BlockTest(unittest.TestCase):
    def test_round_trip_keeps_flags_and_payload(self) -> None:
        rows = [
            Record(Item("alpha", b"payload", False, STAMP, STAMP + 1)),
            Record(Item("beta", b"\x00\x01", True, STAMP, STAMP), True),
        ]
        back = decode_block(encode_block(rows))
        self.assertEqual([row.item.id for row in back], ["alpha", "beta"])
        self.assertTrue(back[1].item.sensitive)
        self.assertFalse(back[0].deleted)
        self.assertEqual(back[1].item.data, b"\x00\x01")
        self.assertTrue(back[1].deleted)

    def test_header_magic_and_version(self) -> None:
        blob = encode_block([Record(Item("a", b"x"))])
        self.assertEqual(blob[:8], model.BLOCK_MAGIC)
        self.assertEqual(blob[8:10], b"\x00\x01")

    def test_rejects_bad_magic(self) -> None:
        blob = bytearray(encode_block([Record(Item("a", b"x"))]))
        blob[0] = ord("X")
        with self.assertRaises(BadStore):
            decode_block(bytes(blob))

    def test_rejects_unsupported_version(self) -> None:
        blob = bytearray(encode_block([Record(Item("a", b"x"))]))
        blob[8:10] = b"\x00\x09"
        with self.assertRaises(BadStore):
            decode_block(bytes(blob))

    def test_rejects_truncated_payload(self) -> None:
        blob = encode_block([Record(Item("a", b"payload"))])
        with self.assertRaises(BadStore):
            decode_block(blob[:-2])

    def test_rejects_trailing_bytes(self) -> None:
        blob = encode_block([Record(Item("a", b"x"))]) + b"junk"
        with self.assertRaises(BadStore):
            decode_block(blob)

    def test_rejects_duplicate_ids(self) -> None:
        rows = [Record(Item("dup", b"a")), Record(Item("dup", b"b"))]
        with self.assertRaises(BadStore):
            decode_block(encode_block(rows))

    def test_empty_block_round_trip(self) -> None:
        self.assertEqual(decode_block(encode_block([])), [])


class ValidateTest(unittest.TestCase):
    def test_level_and_method_names(self) -> None:
        self.assertIs(parse_level("l4"), Level.L4)
        self.assertIs(parse_level(Level.L3), Level.L3)
        self.assertIs(parse_method("SECURE"), Method.SECURE)
        self.assertIs(parse_method(Method.CRYPTO), Method.CRYPTO)

    def test_level_and_method_reject_unknown(self) -> None:
        with self.assertRaises(BadInput):
            parse_level("L1")
        with self.assertRaises(BadInput):
            parse_method("shred")

    def test_agent_id_rules(self) -> None:
        self.assertEqual(check_id(" 007 "), "7")
        for bad in ("", "0", "-1", "abc", "../1"):
            with self.assertRaises(BadInput):
                check_id(bad)

    def test_item_id_rules(self) -> None:
        self.assertEqual(check_item("item-1.a:b_c"), "item-1.a:b_c")
        for bad in ("", "-lead", "has space", "x" * 65):
            with self.assertRaises(BadInput):
                check_item(bad)

    def test_item_rejects_non_bytes(self) -> None:
        with self.assertRaises(BadInput):
            Item("a", "text")  # type: ignore[arg-type]

    def test_item_rejects_negative_stamp(self) -> None:
        with self.assertRaises(BadInput):
            Item("a", b"x", created_at=-1)

    def test_parse_items_dedupes(self) -> None:
        self.assertEqual(parse_items("a,b,a"), ["a", "b"])
        self.assertEqual(parse_items(["x", "y"]), ["x", "y"])
        with self.assertRaises(BadInput):
            parse_items(" , ")

    def test_block_names(self) -> None:
        self.assertEqual(block_name(7), "000007.airyblk")
        self.assertEqual(check_block("000123.airyblk"), "000123.airyblk")
        for bad in ("../000001.airyblk", "1.airyblk", "000000.airyblk"):
            with self.assertRaises(BadStore):
                check_block(bad)

    def test_iso_round_trip(self) -> None:
        self.assertEqual(parse_iso(iso(STAMP)), STAMP)
        self.assertTrue(iso(STAMP).endswith("Z"))

    def test_window_rules(self) -> None:
        self.assertIn("02:00-05:00", model.check_window("02:00-05:00"))
        with self.assertRaises(BadConfig):
            model.check_window("25:00-05:00")
        moment = datetime.datetime(2026, 3, 4, 3, 30).timestamp()
        self.assertTrue(in_window("02:00-05:00", int(moment * model.NS)))
        self.assertTrue(in_window("23:00-04:00", int(moment * model.NS)))


class ExitCodeTest(unittest.TestCase):
    def test_mapping(self) -> None:
        self.assertEqual(exit_code(BadInput("x")), 2)
        self.assertEqual(exit_code(BadStore("x")), 3)
        self.assertEqual(exit_code(BadUnsupported("x")), 3)
        self.assertEqual(exit_code(BadPerm("x")), 4)
        self.assertEqual(exit_code(ValueError("x")), 2)
        self.assertEqual(exit_code(RuntimeError("x")), 4)

    def test_crypto_is_rejected(self) -> None:
        with self.assertRaises(BadUnsupported) as caught:
            unsupported(Method.CRYPTO)
        self.assertEqual(caught.exception.code, 3)
        self.assertIn("本版不提供", str(caught.exception))
        unsupported(Method.SECURE)


if __name__ == "__main__":
    unittest.main()
