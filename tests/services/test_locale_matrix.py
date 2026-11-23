# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# locale 矩阵测试
#
# 判定必须能分开「数据文件本身坏了」与「配置引用了矩阵外的项」：
# 前者由 problems 报，后者由 conf_problems 报。两类用例都覆盖，避免
# 只测正确数据导致校验形同虚设。

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airylocale import matrix  # noqa: E402


class MatrixTest(unittest.TestCase):
    def test_repository_matrix_is_valid(self) -> None:
        rows = matrix.load()
        self.assertEqual(len(rows), matrix.WANT)
        self.assertEqual(matrix.problems(rows), [])
        self.assertIn(matrix.DEFAULT, {row.posix for row in rows})

    def test_priorities_cover_two_p0(self) -> None:
        rows = matrix.load()
        p0 = [row.posix for row in rows if row.prio == "P0"]
        self.assertEqual(p0, ["en_US.UTF-8", "zh_CN.UTF-8"])

    def test_bad_column_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.tsv"
            path.write_text("en_US.UTF-8\ten\tUS\tUTF-8\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                matrix.load(path)

    def test_problems_flag_structure(self) -> None:
        rows = [
            matrix.Row("en_US.UTF-8", "en", "US", "UTF-8", "P0"),
            matrix.Row("en_US.UTF-8", "en", "US", "UTF-8", "P0"),
            matrix.Row("zh_CN.ISO-8859-1", "zh", "CN", "ISO-8859-1", "P9"),
        ]
        bad = "\n".join(matrix.problems(rows))
        self.assertIn("条目重复", bad)
        self.assertIn("优先级", bad)
        self.assertIn("UTF-8", bad)
        self.assertIn("条目数", bad)
        no_default = [matrix.Row("de_DE.UTF-8", "de", "DE", "UTF-8", "P2")]
        self.assertIn("缺少默认", "\n".join(matrix.problems(no_default)))

    def test_source_joins_language_and_territory(self) -> None:
        row = matrix.Row("zh_CN.UTF-8", "zh", "CN", "UTF-8", "P0")
        self.assertEqual(row.source, "zh_CN")
        bare = matrix.Row("en.UTF-8", "en", "", "UTF-8", "P2")
        self.assertEqual(bare.source, "en")


class ConfTest(unittest.TestCase):
    def test_read_conf_skips_comments_and_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "locale.conf"
            path.write_text(
                "# 注释\nLANG=\"zh_CN.UTF-8\"\nLC_TIME=en_US.UTF-8\n",
                encoding="utf-8")
            env = matrix.read_conf(path)
        self.assertEqual(env, {"LANG": "zh_CN.UTF-8", "LC_TIME": "en_US.UTF-8"})

    def test_conf_problems(self) -> None:
        rows = matrix.load()
        self.assertEqual(matrix.conf_problems({"LANG": "zh_CN.UTF-8"}, rows), [])
        bad = matrix.conf_problems({"LC_TIME": "xx_YY.UTF-8"}, rows)
        self.assertIn("缺少 LANG", "\n".join(bad))
        self.assertIn("不在矩阵内", "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
