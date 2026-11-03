# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 内存与换页上限补齐的回归测试
#
# 临时机器上分别搭出 hybrid v1（memsw 接口）、统一 v2（memory.swap.max）
# 与两种不可用形态（未开 swapaccount、整棵记账树缺失），验证判定、写入
# 与拒写守卫。百分比一律以 1 GB 的伪 MemTotal 换算，断言用整数比较，
# 不依赖宿主机内存大小。

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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from airymax_rt import cli, memcap  # noqa: E402

TOTAL = 1024000000

AGENT = "airymax-agent.slice"

SESSION = "user.slice.d/50-airymax-memory.conf"


def run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


class Tree(unittest.TestCase):
    """一台临时机器：MemTotal 固定 1 GB，声明与 cgroup 树按用例搭建。"""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name)
        self.meminfo = self.base / "meminfo"
        self.meminfo.write_text(
            "MemTotal:       1000000 kB\n", encoding="ascii"
        )
        self.units = self.base / "units"
        (self.units / "user.slice.d").mkdir(parents=True)
        self.unit(AGENT, "MemoryMax=90%\nMemorySwapMax=10%\n")
        self.unit(
            SESSION,
            "[Slice]\nMemoryMax=85%\nMemorySwapMax=15%\n",
        )
        self.root = self.base / "cgroup"
        self.root.mkdir()

    def unit(self, name: str, text: str) -> None:
        (self.units / name).write_text(text, encoding="utf-8")

    def file(self, name: str, text: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="ascii")
        return path

    def v1(
        self,
        usage: str = "0",
        mem: str = "infinity",
        memusage: str = "0",
    ) -> None:
        """hybrid 层级：memsw 与内存接口齐全，两个 slice 目录已存在。"""
        self.file("memory/memory.memsw.limit_in_bytes", "infinity")
        for home in ("airymax.slice/airymax-agent.slice", "user.slice"):
            self.file(f"memory/{home}/memory.limit_in_bytes", mem)
            self.file(f"memory/{home}/memory.usage_in_bytes", memusage)
            self.file(f"memory/{home}/memory.memsw.limit_in_bytes", "infinity")
            self.file(f"memory/{home}/memory.memsw.usage_in_bytes", usage)

    def v2(self, swap: str = "max") -> None:
        self.file("cgroup.controllers", "cpu memory")
        for home in ("airymax.slice/airymax-agent.slice", "user.slice"):
            self.file(f"{home}/memory.swap.max", swap)

    def value(self, home: str, name: str) -> str:
        path = self.root / home / name
        return path.read_text(encoding="ascii").strip()

    def args(self, cmd: str = "memcap") -> list[str]:
        return [
            cmd,
            "--cgroup",
            str(self.root),
            "--unitdir",
            str(self.units),
        ]

    def inspect(self) -> memcap.Report:
        return memcap.inspect(
            str(self.root), str(self.units), str(self.meminfo)
        )

    def apply(self) -> memcap.Report:
        return memcap.apply(str(self.root), str(self.units), str(self.meminfo))


class V1Case(Tree):
    """hybrid 层级必须补写 memsw，且不得在超用时动刀。"""

    def test_apply_writes_total(self) -> None:
        self.v1()
        found = self.apply()
        self.assertEqual([item.state for item in found.steps], ["applied"] * 2)
        for home in ("airymax.slice/airymax-agent.slice", "user.slice"):
            self.assertEqual(
                self.value(f"memory/{home}", memcap.V1_MEMSW), str(TOTAL)
            )

    def test_apply_is_idempotent(self) -> None:
        self.v1()
        self.apply()
        found = self.apply()
        self.assertEqual([item.state for item in found.steps], ["ok"] * 2)

    def test_memory_limit_first(self) -> None:
        """memsw 不得低于内存上限，先把声明里的 MemoryMax 落地。"""
        self.v1()
        self.apply()
        homes = (
            ("airymax.slice/airymax-agent.slice", "921600000"),
            ("user.slice", "870400000"),
        )
        for home, want in homes:
            self.assertEqual(
                self.value(f"memory/{home}", memcap.V1_MEM), want
            )

    def test_memory_usage_blocks(self) -> None:
        self.v1(memusage=str(TOTAL))
        found = self.apply()
        self.assertEqual([item.state for item in found.steps], ["tight"] * 2)
        self.assertEqual(
            self.value("memory/user.slice", memcap.V1_MEMSW), "infinity"
        )

    def test_inspect_reports_drift(self) -> None:
        self.v1()
        found = self.inspect()
        self.assertEqual(
            [item.state for item in found.steps], [memcap.DIFFERS] * 2
        )
        self.assertEqual(
            self.value("memory/user.slice", memcap.V1_MEMSW), "infinity"
        )

    def test_tight_refuses_write(self) -> None:
        self.v1(usage=str(TOTAL + 1))
        found = self.apply()
        self.assertEqual([item.state for item in found.steps], ["tight"] * 2)
        self.assertEqual(
            self.value("memory/user.slice", memcap.V1_MEMSW), "infinity"
        )

    def test_missing_memsw(self) -> None:
        self.file("memory/memory.memsw.usage_in_bytes", "0")
        found = self.inspect()
        self.assertEqual(found.layout, "v1")
        self.assertEqual([item.state for item in found.steps], ["absent"] * 2)
        self.assertIn("memsw", found.steps[0].note)


class PageCase(Tree):
    """上限按页对齐：内核向下取整，目标不对齐会把落地误判成 absent。"""

    def align(self) -> None:
        self.meminfo.write_text(
            "MemTotal:       1000003 kB\n", encoding="ascii"
        )

    def test_bounds_aligned(self) -> None:
        self.align()
        found = memcap.bounds(str(self.units), str(self.meminfo))
        self.assertEqual(len(found), 2)
        for item in found:
            self.assertEqual(item.mem % memcap._page(), 0)
            self.assertEqual(item.total % memcap._page(), 0)

    def test_apply_aligned(self) -> None:
        self.align()
        self.v1()
        found = self.apply()
        self.assertEqual([item.state for item in found.steps], ["applied"] * 2)
        for home in ("airymax.slice/airymax-agent.slice", "user.slice"):
            text = self.value(f"memory/{home}", memcap.V1_MEMSW)
            self.assertEqual(int(text, 10) % memcap._page(), 0)


class V2Case(Tree):
    """统一层级上 systemd 已写好，正常路径只核对。"""

    def test_written(self) -> None:
        self.v2(str(102400000))
        self.file("user.slice/memory.swap.max", "153600000")
        found = self.inspect()
        self.assertEqual(found.layout, "v2")
        self.assertEqual([item.state for item in found.steps], ["ok"] * 2)

    def test_unlimited_is_drift(self) -> None:
        self.v2()
        found = self.apply()
        self.assertEqual(
            [item.state for item in found.steps], ["applied"] * 2
        )
        self.assertEqual(
            self.value("airymax.slice/airymax-agent.slice", "memory.swap.max"),
            "102400000",
        )


class AbsentCase(Tree):
    """记账树或声明缺失时不得假装通过。"""

    def test_no_layout(self) -> None:
        found = self.inspect()
        self.assertEqual(found.layout, "")
        self.assertEqual(found.steps, [])

    def test_no_units(self) -> None:
        for name in (AGENT, SESSION):
            (self.units / name).unlink()
        self.v1()
        self.assertEqual(self.inspect().steps, [])


class CliCase(Tree):
    """退出码与 JSON 形状，供门禁直接判读。"""

    def test_inspect_partial(self) -> None:
        self.v1()
        code, out = run(self.args())
        self.assertEqual(code, cli.EXIT_PARTIAL)
        self.assertIn("layout=v1", out)
        self.assertIn("differs", out)

    def test_apply_ok(self) -> None:
        self.v1()
        code, out = run(self.args() + ["--apply"])
        self.assertEqual(code, 0)
        self.assertIn("applied", out)

    def test_json(self) -> None:
        self.v1()
        code, out = run(["--json"] + self.args())
        self.assertEqual(code, cli.EXIT_PARTIAL)
        payload = json.loads(out)
        self.assertEqual(payload["layout"], "v1")
        self.assertEqual(payload["steps"][1]["path"], "user.slice")

    def test_tight_partial(self) -> None:
        # 命令行按宿主机真实 MemTotal 换算，取 1 TiB 保证超过任何目标。
        self.v1(usage=str(1 << 40))
        code, out = run(self.args() + ["--apply"])
        self.assertEqual(code, cli.EXIT_PARTIAL)
        self.assertIn("在用量已超目标", out)

    def test_no_layout_fails(self) -> None:
        code, _ = run(self.args())
        self.assertEqual(code, cli.EXIT_FAIL)


if __name__ == "__main__":
    unittest.main()