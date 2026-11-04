# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 固件加载实况的单元测试
#
# 覆盖四条判据：期望文件名只来自 modinfo、实况只来自内核日志、没有证据时
# 判 unknown（不是 loaded）、三类失败签名各自成警告。另有一条端到端用例
# 走命令行：假 modinfo + 假日志，钉住退出码与首行字段。

"""Firmware survey tests."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airymax_observe import cli, fw  # noqa: E402

LOG = """\
[    2.100] iwlwifi 0000:00:14.3: Direct firmware load for \
iwlwifi-ty-a0-gf-a0-92.ucode failed with error -2
[    2.200] iwlwifi 0000:00:14.3: firmware: direct-loading firmware \
iwlwifi-ty-a0-gf-a0-89.ucode
[    3.400] rtw89_8852be 0000:02:00.0: firmware: failed to load \
rtw89/rtw8852b_fw-1.bin (-2)
"""


class Tree(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def put(self, name: str, text: str) -> Path:
        path = self.base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class Parse(Tree):
    def test_load_list(self) -> None:
        """/proc/modules 首列即模块名，其余字段不参与。"""
        path = self.put("modules", "wl 16384 0 - Live 0x0\nbt 4096 1 wl,"
                                  " Live 0x0\n")
        self.assertEqual(fw.load_list(path), ("wl", "bt"))

    def test_load_list_absent(self) -> None:
        """读不到模块表时为空：调用方据 empty 退出，而不是编一个列表。"""
        self.assertEqual(fw.load_list(self.base / "gone"), ())

    def test_kernel_log(self) -> None:
        path = self.put("dmesg", LOG)
        self.assertEqual(fw.kernel_log(str(path)), LOG)

    def test_kernel_log_absent(self) -> None:
        self.assertEqual(fw.kernel_log(str(self.base / "gone")), "")

    def test_expected_absent_module(self) -> None:
        """modinfo 失败（模块名不对）时不给任何期望文件名。"""
        self.assertEqual(fw.expected("no-such-module-xyz"), ())


class Collect(Tree):
    def survey(self, log: str = LOG) -> fw.Survey:
        want = {"wifi": ("iwlwifi-ty-a0-gf-a0-92.ucode",
                         "iwlwifi-ty-a0-gf-a0-89.ucode"),
                "wlan": ("rtw89/rtw8852b_fw-1.bin", "rtw89/rtw8852b_fw-2.bin")}
        return fw.collect(want, log, lambda mod: want[mod])

    def test_states(self) -> None:
        """失败、成功、无证据各归各位；同名不同目录按文件名对齐。"""
        found = self.survey()
        self.assertEqual(
            [(item.module, item.file, item.state) for item in found.items],
            [("wifi", "iwlwifi-ty-a0-gf-a0-92.ucode", fw.MISSING),
             ("wifi", "iwlwifi-ty-a0-gf-a0-89.ucode", fw.LOADED),
             ("wlan", "rtw89/rtw8852b_fw-1.bin", fw.MISSING),
             ("wlan", "rtw89/rtw8852b_fw-2.bin", fw.UNKNOWN)])

    def test_counts(self) -> None:
        found = self.survey()
        self.assertEqual(found.count(fw.LOADED), 1)
        self.assertEqual(found.count(fw.MISSING), 2)
        self.assertEqual(found.count(fw.UNKNOWN), 1)
        self.assertEqual(found.modules, ("wifi", "wlan"))
        self.assertEqual(found.names(fw.MISSING),
                         ("wifi:iwlwifi-ty-a0-gf-a0-92.ucode",
                          "wlan:rtw89/rtw8852b_fw-1.bin"))

    def test_no_log_is_unknown(self) -> None:
        """日志缺失时全部 unknown——没有证据不等于载入成功。"""
        found = self.survey("")
        self.assertEqual(found.count(fw.LOADED), 0)
        self.assertEqual(found.count(fw.UNKNOWN), 4)

    def test_warnings(self) -> None:
        """三类失败签名各命中一次，重复行只报一次。"""
        log = ("[ 1.0] iwlwifi: no suitable firmware found!\n"
               "[ 1.1] iwlwifi: no suitable firmware found!\n"
               "[ 1.2] microcode: Failed to run INIT ucode: -110\n"
               "[ 1.3] iwlwifi: api flags index 3 larger than supported "
               "by driver\n")
        found = self.survey(log)
        self.assertEqual(len(found.warnings), 3)
        self.assertEqual(found.count(fw.UNKNOWN), 4)

    def test_dump(self) -> None:
        found = self.survey()
        dumped = found.dump()
        self.assertEqual(dumped["files"], 4)
        self.assertEqual(dumped["loaded"], 1)
        self.assertEqual(len(dumped["missing"]), 2)
        self.assertEqual(len(dumped["unknown"]), 1)
        self.assertEqual([mod["module"] for mod in dumped["modules"]],
                         ["wifi", "wlan"])


class Cli(Tree):
    def setUp(self) -> None:
        super().setUp()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.mods = self.put("modules", "wl 16384 0 - Live 0x0\n")
        self.log = self.put("dmesg", "")
        fake = self.put("bin/modinfo",
                        "#!/bin/sh\n"
                        "printf '%s\\n' wl-a.bin wl-b.bin\n")
        fake.chmod(0o755)

    def run_cli(self, *argv: str) -> tuple[int, str]:
        buf = io.StringIO()
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}")
        with contextlib.redirect_stdout(buf):
            with mock.patch.dict(os.environ, env):
                code = cli.main(list(argv))
        return code, buf.getvalue()

    def args(self) -> tuple[str, ...]:
        return ("firmware", "--modules", str(self.mods),
                "--dmesg", str(self.log))

    def test_ok(self) -> None:
        """两项都有成功证据时状态 ok、退出 0，模块行逐项给状态。"""
        self.log.write_text("[ 1.0] firmware: direct-loading firmware "
                            "wl-a.bin\n[ 1.1] firmware: direct-loading "
                            "firmware wl-b.bin\n", encoding="utf-8")
        code, out = self.run_cli(*self.args())
        self.assertEqual(code, 0)
        self.assertIn("airy-observe firmware state=ok modules=1 files=2"
                      " loaded=2 missing=0 unknown=0 warnings=0", out)
        self.assertIn("wl-a.bin=loaded", out)
        self.assertIn("wl-b.bin=loaded", out)

    def test_partial(self) -> None:
        """没有证据判 partial（退 3）：unknown 不是通过。"""
        code, out = self.run_cli(*self.args())
        self.assertEqual(code, cli.EXIT_PARTIAL)
        self.assertIn("state=partial", out)
        self.assertIn("no-evidence", out)

    def test_missing(self) -> None:
        """载入失败判 missing（退 1），并逐项列出模块:文件。"""
        self.log.write_text("[ 1.0] firmware: failed to load wl-b.bin (-2)\n",
                            encoding="utf-8")
        code, out = self.run_cli(*self.args())
        self.assertEqual(code, cli.EXIT_FAIL)
        self.assertIn("state=missing", out)
        self.assertIn("missing wl:wl-b.bin", out)

    def test_warning(self) -> None:
        """失败签名即使不落在期望文件名上，也判 missing。"""
        self.log.write_text("[ 1.0] iwlwifi: api flags index 4 larger than"
                            " supported by driver\n", encoding="utf-8")
        code, out = self.run_cli(*self.args())
        self.assertEqual(code, cli.EXIT_FAIL)
        self.assertIn("warning [ 1.0] iwlwifi: api flags index 4 larger",
                      out)

    def test_json(self) -> None:
        code, out = self.run_cli(*self.args(), "--json")
        body = json.loads(out)
        self.assertEqual(code, cli.EXIT_PARTIAL)
        self.assertEqual(body["modules"][0]["module"], "wl")
        self.assertEqual(len(body["modules"][0]["files"]), 2)
        self.assertEqual(body["warnings"], [])


if __name__ == "__main__":
    unittest.main()
