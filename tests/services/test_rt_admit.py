# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 新实例准入判定的回归测试
#
# MemTotal 固定 1 GB，agent 侧上限 90%，因此保留量恰好一成：判定只做整数
# 加减，不依赖宿主机内存大小。拒绝路径必须同时给出缺口字节数与退出码 3，
# 缺 --need 按用法错误退出 2，二者不能混成一个。

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airymax_rt import admit, cli  # noqa: E402

TOTAL = 1024000000

AVAIL = 500000 * 1024

KEEP = TOTAL // 10

AGENT = "airymax-agent.slice"

SESSION = "user.slice.d/50-airymax-memory.conf"


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out):
        with contextlib.redirect_stderr(err):
            code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class Tree(unittest.TestCase):
    """一台临时机器：内存接口与两份单元声明按用例搭建。"""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name)
        self.meminfo = self.meminfo_at("MemAvailable:  500000 kB\n")
        self.units = self.base / "units"
        (self.units / "user.slice.d").mkdir(parents=True)
        self.unit(AGENT, "MemoryMax=90%\nMemorySwapMax=10%\n")
        self.unit(
            SESSION,
            "[Slice]\nMemoryMax=85%\nMemorySwapMax=15%\n",
        )

    def meminfo_at(self, extra: str, name: str = "meminfo") -> str:
        path = self.base / name
        path.write_text(
            f"MemTotal:       1000000 kB\n{extra}", encoding="ascii"
        )
        return str(path)

    def unit(self, name: str, text: str) -> None:
        (self.units / name).write_text(text, encoding="utf-8")

    def args(self, need: str) -> list[str]:
        return [
            "admit",
            "--need",
            need,
            "--meminfo",
            self.meminfo,
            "--unitdir",
            str(self.units),
        ]


class SizeTest(unittest.TestCase):
    """尺寸解析：后缀、纯字节与不可判读。"""

    def test_gigabytes(self) -> None:
        self.assertEqual(admit.size("12G"), 12 << 30)

    def test_megabytes(self) -> None:
        self.assertEqual(admit.size("512M"), 512 << 20)

    def test_iec(self) -> None:
        self.assertEqual(admit.size("1GiB"), 1 << 30)

    def test_plain(self) -> None:
        self.assertEqual(admit.size("4096"), 4096)

    def test_bad_suffix(self) -> None:
        self.assertEqual(admit.size("12X"), 0)

    def test_empty(self) -> None:
        self.assertEqual(admit.size(""), 0)

    def test_no_number(self) -> None:
        self.assertEqual(admit.size("G"), 0)


class CheckTest(Tree):
    """判定层：保留量来自单元声明，可用内存扣除保留量后才比较。"""

    def test_reserve(self) -> None:
        self.assertEqual(admit.reserve(str(self.units), self.meminfo,
                                      TOTAL), TOTAL // 10)

    def test_reserve_fallback(self) -> None:
        """没有声明时按一成保留，不把整机让给第一个实例。"""
        self.unit(AGENT, "")
        self.unit(SESSION, "")
        self.assertEqual(admit.reserve(str(self.units), self.meminfo,
                                      TOTAL), TOTAL // 10)

    def test_fit(self) -> None:
        found = admit.check(300 << 20, self.meminfo, str(self.units))
        self.assertEqual(found.state, admit.FIT)
        self.assertEqual(found.short, 0)
        self.assertEqual(found.keep, TOTAL // 10)

    def test_refuse(self) -> None:
        found = admit.check(500 << 20, self.meminfo, str(self.units))
        self.assertEqual(found.state, admit.REFUSE)
        self.assertEqual(found.short, (500 << 20) - (AVAIL - KEEP))

    def test_exact(self) -> None:
        found = admit.check(AVAIL - KEEP, self.meminfo, str(self.units))
        self.assertEqual(found.state, admit.FIT)
        self.assertEqual(found.short, 0)

    def test_tight(self) -> None:
        """可用量低于保留量时可用余额按 0 计，不出现负数。"""
        self.meminfo = self.meminfo_at(
            "MemAvailable:   50000 kB\n", "tight"
        )
        found = admit.check(1 << 20, self.meminfo, str(self.units))
        self.assertEqual(found.state, admit.REFUSE)
        self.assertEqual(found.free, 0)

    def test_unknown(self) -> None:
        found = admit.check(1 << 20, str(self.base / "absent"),
                            str(self.units))
        self.assertEqual(found.state, admit.UNKNOWN)
        self.assertIn("不可读", found.note)


class CliTest(Tree):
    """命令行层：装得下返回 0，拒绝返回 3 并给出缺口。"""

    def test_fit(self) -> None:
        code, out, _ = run(self.args("300M"))
        self.assertEqual(code, 0)
        self.assertIn("admit=fit", out)
        self.assertIn("short=0", out)

    def test_refuse(self) -> None:
        code, out, _ = run(self.args("500M"))
        self.assertEqual(code, cli.EXIT_PARTIAL)
        self.assertIn("admit=refuse", out)
        self.assertIn(f"short={(500 << 20) - (AVAIL - KEEP)}", out)

    def test_json(self) -> None:
        code, out, _ = run(["--json"] + self.args("500M"))
        self.assertEqual(code, cli.EXIT_PARTIAL)
        payload = json.loads(out)
        self.assertEqual(payload["state"], admit.REFUSE)
        self.assertEqual(payload["short"], (500 << 20) - (AVAIL - KEEP))
        self.assertEqual(payload["reserve"], KEEP)

    def test_missing_need(self) -> None:
        code, _, err = run(["admit", "--meminfo", self.meminfo])
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("--need", err)


if __name__ == "__main__":
    unittest.main()
