# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 系统清点的单元测试
#
# 钉住四件事：三个来源各自只认自己的证据格式；任一来源读不到时该面为
# unknown 而不是 ok；缺包与多包分列两边；命令行退出码 0/1/3 可区分。
# 判据来自 0.0.4 施工图 V-15：装机后 failed 单元与 err 日志均为 0 行，
# 镜像内包闭包与交付清单差为 0 且无孤儿包。

"""System inventory tests."""

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

from airymax_observe import cli, sys as osys  # noqa: E402

UNITS = """\
airymax-data.mount loaded failed failed /data
airy-llm.service loaded failed failed AirymaxOS local model
not-a-unit loaded failed failed ignored
airymax-data.mount loaded failed failed duplicated
"""

JOURNAL = """\
[  12.345] iwlwifi: firmware load failed
[  13.000] bluetooth: no controller
[  13.000] bluetooth: no controller
"""

MANIFEST = """\
# 镜像交付的包集合
airymaxos-system
ukui-session-manager
"""

RPM = "airymaxos-system\nukui-session-manager\n"

DRIFT = "airymaxos-system\nukui-session-manager\nextra-pkg\n"


class ParseTest(unittest.TestCase):
    def test_units_keep_order_and_drop_noise(self) -> None:
        self.assertEqual(
            osys.failed_units(UNITS),
            ("airymax-data.mount", "airy-llm.service"),
        )

    def test_journal_dedup_and_keep_order(self) -> None:
        self.assertEqual(
            osys.journal_errs(JOURNAL),
            ("iwlwifi: firmware load failed", "bluetooth: no controller"),
        )

    def test_declared_skips_comments(self) -> None:
        self.assertEqual(
            osys.declared(MANIFEST),
            ("airymaxos-system", "ukui-session-manager"),
        )

    def test_diff_splits_both_sides(self) -> None:
        missing, extra = osys.diff(
            osys.declared(MANIFEST), osys.installed(DRIFT),
        )
        self.assertEqual(missing, ())
        self.assertEqual(extra, ("extra-pkg",))

    def test_diff_reports_missing(self) -> None:
        missing, extra = osys.diff(
            osys.declared(MANIFEST), osys.installed("airymaxos-system\n"),
        )
        self.assertEqual(missing, ("ukui-session-manager",))
        self.assertEqual(extra, ())


class SurveyTest(unittest.TestCase):
    def test_clean(self) -> None:
        found = osys.collect("", "", RPM, MANIFEST)
        self.assertEqual(found.state, osys.STATE_OK)
        self.assertEqual(found.units_state, osys.STATE_OK)
        self.assertEqual(found.logs_state, osys.STATE_OK)
        self.assertEqual(found.packs_state, osys.STATE_OK)
        self.assertEqual((found.want_n, found.have_n), (2, 2))

    def test_failed_unit_wins(self) -> None:
        found = osys.collect(UNITS, JOURNAL, RPM, MANIFEST)
        self.assertEqual(found.state, osys.STATE_FAILED)
        self.assertEqual(found.units_state, osys.STATE_FAILED)
        self.assertEqual(found.logs_state, osys.STATE_ERRORS)

    def test_missing_manifest_is_unknown(self) -> None:
        found = osys.collect("", "", RPM, None)
        self.assertEqual(found.packs_state, osys.STATE_UNKNOWN)
        self.assertEqual(found.state, osys.STATE_UNKNOWN)

    def test_missing_journal_is_unknown(self) -> None:
        found = osys.collect("", None, RPM, MANIFEST)
        self.assertEqual(found.logs_state, osys.STATE_UNKNOWN)


class CommandTest(unittest.TestCase):
    def run_cli(self, *argv: str) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
        return code, buf.getvalue()

    def files(self, units: str, journal: str, rpm: str,
              manifest: str | None) -> list[str]:
        tmp = tempfile.mkdtemp()
        paths = []
        for name, body in (("units", units), ("journal", journal),
                           ("rpm", rpm)):
            path = Path(tmp, name)
            path.write_text(body, encoding="utf-8")
            paths.append(str(path))
        if manifest is not None:
            path = Path(tmp, "manifest")
            path.write_text(manifest, encoding="utf-8")
            paths.append(str(path))
        return paths

    def test_clean_exit_zero(self) -> None:
        units, journal, rpm, manifest = self.files("", "", RPM, MANIFEST)
        code, out = self.run_cli(
            "system", "--json", "--units", units, "--journal", journal,
            "--rpm", rpm, "--manifest", manifest,
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["state"], "ok")

    def test_failed_exit_one(self) -> None:
        units, journal, rpm, manifest = self.files(UNITS, "", RPM, MANIFEST)
        code, out = self.run_cli(
            "system", "--units", units, "--journal", journal,
            "--rpm", rpm, "--manifest", manifest,
        )
        self.assertEqual(code, 1)
        self.assertIn("failed airymax-data.mount", out)

    def test_unreadable_exit_three(self) -> None:
        units, journal, rpm = self.files("", "", RPM, None)
        code, out = self.run_cli(
            "system", "--units", units, "--journal", journal,
            "--rpm", rpm, "--manifest", str(Path(rpm, "absent")),
        )
        self.assertEqual(code, 3, out)
        self.assertIn("state=unknown", out)


if __name__ == "__main__":
    unittest.main()
