# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 系统清点的单元测试
#
# 钉住五件事：三个来源各自只认自己的证据格式；任一来源读不到时该面为
# unknown 而不是 ok；缺包与多包分列两边；err 按单元归属分成自有／外来／
# 未知三层，自有与未知无条件失败、外来只与预算比较；命令行退出码 0/1/3
# 可区分。
#
# 分层与预算的由来（285H 实测）：基底自身会打印消除不掉的 err（rtkit 拿
# 不到实时优先级、ACPI 表重复），「任何 err 即失败」在真机上长期为红，
# 判据就失去信号价值；而 pid 每次不同会让同一崩溃在基线里重复计数，故先
# 归一化再按「内容 + 单元」去重。

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


def rec(msg: str, unit: str = "", ident: str = "", **extra: str) -> str:
    """一条 journald JSON 记录；只填判定真正要读的字段。"""
    item = {"MESSAGE": msg}
    if unit:
        item["_SYSTEMD_UNIT"] = unit
    if ident:
        item["SYSLOG_IDENTIFIER"] = ident
    item.update(extra)
    return json.dumps(item)


FOREIGN = rec("alsa: cannot open card", "cups.service", "cupsd")

OWNED = rec("cannot bind socket", "airymax-desktop.service")

# systemd 自己发的单元消息：_SYSTEMD_UNIT 是发起者 init.scope，被谈论的
# 单元只在 UNIT 字段里（285H 的 journald JSON 取证）。
JOB_FAIL = rec("Failed to start AirymaxOS acceptance smoke report.",
               "init.scope", "systemd",
               UNIT="airymax-smoke-report.service")

# ACPICA 的表缺陷：内核转发、文本以「ACPI BIOS Error (bug)」「ACPI Error」
# 开头，属于主板 DSDT 的问题，不进外来预算。
FIRMWARE = rec("ACPI BIOS Error (bug): Failure creating named object",
               ident="kernel", _TRANSPORT="kernel")
FIRMWARE_TZ = rec("ACPI Error: Aborting method \\_TZ.TZ00._TMP due to "
                  "previous error (AE_NOT_FOUND)", ident="kernel",
                  _TRANSPORT="kernel")

# 内核的其他 err 不是固件缺陷，仍按外来层计数。
KERNEL_IO = rec("blk_update_request: I/O error, dev sda", ident="kernel",
                _TRANSPORT="kernel")


class ParseTest(unittest.TestCase):
    def test_units_keep_order_and_drop_noise(self) -> None:
        self.assertEqual(
            osys.failed_units(UNITS),
            ("airymax-data.mount", "airy-llm.service"),
        )

    def test_plain_journal_dedup_and_keep_order(self) -> None:
        # 纯文本行拿不到归属，按未知层记；去重按(内容, 单元)仍成立
        errs = osys.journal_errs(JOURNAL)
        self.assertEqual(
            [err.text for err in errs],
            ["iwlwifi: firmware load failed", "bluetooth: no controller"],
        )
        self.assertTrue(all(not err.known for err in errs))

    def test_json_names_the_owning_unit(self) -> None:
        err = osys.journal_errs(FOREIGN)[0]
        self.assertEqual(err.unit, "cups.service")
        self.assertTrue(err.known)
        self.assertFalse(err.owned)

    def test_identifier_decides_owner_without_unit(self) -> None:
        # 无单元的日志（内核与早期用户态）只能靠 identifier 归属
        err = osys.journal_errs(rec("no card", ident="airy-llm"))[0]
        self.assertTrue(err.owned)
        self.assertEqual(err.unit, "airy-llm")

    def test_unit_field_names_the_subject(self) -> None:
        # systemd 的「Failed to start X.」由 PID 1 落盘：_SYSTEMD_UNIT 是
        # 发起者 init.scope，被谈论的单元在 UNIT。只读前者会把本产品单元
        # 判成外来噪声，判据就永远看不到自有缺陷。
        err = osys.journal_errs(JOB_FAIL)[0]
        self.assertEqual(err.unit, "airymax-smoke-report.service")
        self.assertTrue(err.owned)

    def test_acpi_firmware_is_a_layer(self) -> None:
        # 主板 DSDT 的表缺陷逐字重现且无法由发行版消除，单列固件层
        errs = osys.journal_errs(f"{FIRMWARE}\n{FIRMWARE_TZ}")
        self.assertEqual(len(errs), 2)
        self.assertTrue(all(err.firmware for err in errs))
        self.assertTrue(all(err.known for err in errs))

    def test_kernel_err_stays_foreign(self) -> None:
        # 固件层只认 ACPICA 前缀；内核的其他 err 必须仍被计数
        err = osys.journal_errs(KERNEL_IO)[0]
        self.assertFalse(err.firmware)
        self.assertTrue(err.known)

    def test_pid_normalized_before_dedup(self) -> None:
        # 同一单元反复崩溃时每行只差 pid，不归一化则计数随崩溃次数增长，
        # 预算永远无法收敛
        text = "\n".join((
            rec("Process 1234 (ukui-panel) terminated", "ukui-panel.service"),
            rec("Process 5678 (ukui-panel) terminated", "ukui-panel.service"),
        ))
        errs = osys.journal_errs(text)
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("<pid>", errs[0].text)

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

    def test_counts_split_three_layers(self) -> None:
        text = "\n".join((FOREIGN, OWNED, "[ 1.0] bare line"))
        found = osys.collect("", text, RPM, MANIFEST)
        self.assertEqual(
            (found.owned_n, found.foreign_n, found.unknown_n), (1, 1, 1),
        )

    def test_foreign_within_budget(self) -> None:
        found = osys.collect("", FOREIGN, RPM, MANIFEST, 1)
        self.assertEqual(found.logs_state, osys.STATE_OK)
        self.assertEqual(found.state, osys.STATE_OK)

    def test_foreign_over_budget(self) -> None:
        found = osys.collect("", FOREIGN, RPM, MANIFEST, 0)
        self.assertEqual(found.logs_state, osys.STATE_ERRORS)

    def test_owned_ignores_budget(self) -> None:
        # 自有单元的 err 是本产品缺陷，预算再宽也不能放过
        found = osys.collect("", OWNED, RPM, MANIFEST, 99)
        self.assertEqual(found.logs_state, osys.STATE_ERRORS)

    def test_unknown_layer_ignores_budget(self) -> None:
        found = osys.collect("", JOURNAL, RPM, MANIFEST, 99)
        self.assertEqual(found.logs_state, osys.STATE_ERRORS)

    def test_firmware_layer_ignores_budget(self) -> None:
        # 固件层不判失败，但条数要如实出现在清点里
        text = "\n".join((FIRMWARE, FIRMWARE_TZ))
        found = osys.collect("", text, RPM, MANIFEST, 0)
        self.assertEqual(found.logs_state, osys.STATE_OK)
        self.assertEqual((found.firmware_n, found.foreign_n), (2, 0))
        self.assertEqual(found.state, osys.STATE_OK)

    def test_foreign_count_excludes_firmware(self) -> None:
        # 同一份日志里外来与固件各计各的，互不冲抵
        text = "\n".join((FOREIGN, FIRMWARE, KERNEL_IO))
        found = osys.collect("", text, RPM, MANIFEST, 2)
        self.assertEqual((found.foreign_n, found.firmware_n), (2, 1))
        self.assertEqual(found.logs_state, osys.STATE_OK)

    def test_missing_manifest_is_unknown(self) -> None:
        found = osys.collect("", "", RPM, None)
        self.assertEqual(found.packs_state, osys.STATE_UNKNOWN)
        self.assertEqual(found.state, osys.STATE_UNKNOWN)

    def test_missing_journal_is_unknown(self) -> None:
        found = osys.collect("", None, RPM, MANIFEST)
        self.assertEqual(found.logs_state, osys.STATE_UNKNOWN)

    def test_dump_carries_layers(self) -> None:
        found = osys.collect("", FOREIGN, RPM, MANIFEST, 3)
        logs = found.dump()["logs"]
        self.assertEqual(logs["budget"], 3)
        self.assertEqual(logs["foreign"], 1)
        self.assertEqual(logs["firmware"], 0)
        self.assertEqual(logs["errors"], [json.loads(FOREIGN)["MESSAGE"]])


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

    def test_budget_lifts_foreign_only(self) -> None:
        units, journal, rpm, manifest = self.files("", FOREIGN, RPM, MANIFEST)
        argv = ("system", "--units", units, "--journal", journal,
                "--rpm", rpm, "--manifest", manifest)
        self.assertEqual(self.run_cli(*argv, "--budget", "1")[0], 0)
        code, out = self.run_cli(*argv, "--budget", "0")
        self.assertEqual(code, 1)
        self.assertIn("foreign=1", out)

    def test_owned_marked_in_text(self) -> None:
        units, journal, rpm, manifest = self.files("", OWNED, RPM, MANIFEST)
        code, out = self.run_cli(
            "system", "--units", units, "--journal", journal,
            "--rpm", rpm, "--manifest", manifest, "--budget", "99",
        )
        self.assertEqual(code, 1)
        self.assertIn("owned=1", out)
        self.assertIn("[owned]", out)

    def test_firmware_marked_and_not_gated(self) -> None:
        # 固件层在文本里带层标，预算为 0 也不判失败
        units, journal, rpm, manifest = self.files("", FIRMWARE, RPM, MANIFEST)
        code, out = self.run_cli(
            "system", "--units", units, "--journal", journal,
            "--rpm", rpm, "--manifest", manifest, "--budget", "0",
        )
        self.assertEqual(code, 0, out)
        self.assertIn("firmware=1", out)
        self.assertIn("[firmware]", out)

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
