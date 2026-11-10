# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 系统清点的单元测试
#
# 钉住六件事：三个来源各自只认自己的证据格式；任一来源读不到时该面为
# unknown 而不是 ok；缺包与多包分列两边；err 按归属分四层，自有与未知
# 无条件失败、平台与基底各与自己的预算比较；预算按机器类别分组、平台条目
# 按机器指纹记账、缺组即判负；命令行退出码 0/1/3 可区分。
#
# 分层与预算的由来（285H 实测）：基底与主板都会打印消除不掉的 err（rtkit
# 拿不到实时优先级、ACPI 表重复创建命名对象），「任何 err 即失败」在真机
# 上长期为红，判据就失去信号价值；两类噪声混用一份预算时，收掉一条基底
# 缺陷腾出的额度会被平台噪音吃掉，棘轮看似收紧而实际没有。pid 每次不同会
# 让同一崩溃在基线里重复计数，会话号与 coredump 序号同理，故先归一化再按
# 「内容 + 单元」去重。

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

# 285H 实测的机器指纹（83JK / LNVNB161216 / QLCN24WW），预算文件里的平台
# 条目按它记账。
FP = "3ba63f38b87a"


def rec(msg: str, unit: str = "", ident: str = "", **extra: str) -> str:
    """一条 journald JSON 记录；只填判定真正要读的字段。"""
    item = {"MESSAGE": msg}
    if unit:
        item["_SYSTEMD_UNIT"] = unit
    if ident:
        item["SYSLOG_IDENTIFIER"] = ident
    item.update(extra)
    return json.dumps(item)


BASE_ERR = rec("alsa: cannot open card", "cups.service", "cupsd")

OWNED = rec("cannot bind socket", "airymax-desktop.service")

# systemd 自己发的单元消息：_SYSTEMD_UNIT 是发起者 init.scope，被谈论的
# 单元只在 UNIT 字段里（285H 的 journald JSON 取证）。
JOB_FAIL = rec("Failed to start AirymaxOS acceptance smoke report.",
               "init.scope", "systemd",
               UNIT="airymax-smoke-report.service")

# ACPICA 的表缺陷：内核转发、文本以「ACPI BIOS Error (bug)」「ACPI Error」
# 开头，说的是主板 DSDT 的问题，进平台层预算而不是基底预算。
PLAT = rec("ACPI BIOS Error (bug): Failure creating named object",
           ident="kernel", _TRANSPORT="kernel")
PLAT_TZ = rec("ACPI Error: Aborting method \\_TZ.TZ00._TMP due to "
              "previous error (AE_NOT_FOUND)", ident="kernel",
              _TRANSPORT="kernel")

# 内核的其他 err 不是平台缺陷，仍按基底计数。
KERNEL_IO = rec("blk_update_request: I/O error, dev sda", ident="kernel",
                _TRANSPORT="kernel")

BUDGET = f"""\
# 分组预算：平台条目带机器指纹，基底条目带日期与单元
class=real
platform {FP} 2026-11-10 ACPI BIOS Error (bug): \\_SB.PC00.XHCI.RHUB.HS01._UPC
platform ffffffffffff 2026-11-10 另一块主板的同名噪音
base 2026-11-06 user-session [pulseaudio] pid.c: Daemon already running.

class=qemu
base 2026-11-06 user-session [pulseaudio] pid.c: Daemon already running.
"""


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
        err = osys.journal_errs(BASE_ERR)[0]
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

    def test_acpi_diagnostics_are_platform_layer(self) -> None:
        errs = osys.journal_errs(f"{PLAT}\n{PLAT_TZ}")
        self.assertEqual(len(errs), 2)
        self.assertTrue(all(err.platform for err in errs))
        self.assertTrue(all(err.known for err in errs))

    def test_thermal_zone_warning_is_platform(self) -> None:
        err = osys.journal_errs(rec(
            "thermal thermal_zone3: critical temperature reached",
            ident="kernel", _TRANSPORT="kernel"))[0]
        self.assertTrue(err.platform)

    def test_kernel_err_stays_base(self) -> None:
        # 平台层只认 ACPICA 与热区前缀；内核的其他 err 必须仍被计数
        err = osys.journal_errs(KERNEL_IO)[0]
        self.assertFalse(err.platform)
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

    def test_session_scopes_collapse(self) -> None:
        # 会话号每次登录都换，不收敛时 ldapdb 一行噪声被每个会话各记一次，
        # 预算只随登录次数上涨（285H 实测 22 条）
        text = "\n".join(
            rec("ldapdb_canonuser_plug_init() failed", f"session-{n}.scope")
            for n in (2, 41, 530)
        )
        errs = osys.journal_errs(text)
        self.assertEqual(len(errs), 1, errs)
        self.assertEqual(errs[0].unit, "user-session")

    def test_coredump_units_collapse(self) -> None:
        # coredump 单元名里带序号与 pid，同一崩溃不收敛就会重复计账
        text = "\n".join((
            rec("Process 2306 (kscreen_backend) dumped core.",
                "systemd-coredump@0-22095-0.service"),
            rec("Process 39735 (kscreen_backend) dumped core.",
                "systemd-coredump@4-39780-0.service"),
        ))
        errs = osys.journal_errs(text)
        self.assertEqual(len(errs), 1, errs)
        self.assertEqual(errs[0].unit, "coredump")

    def test_core_dump_belongs_to_owned_layer(self) -> None:
        # 崩溃来自镜像交付的组件，是可修的缺陷；记进预算即承认「可以崩」
        err = osys.journal_errs(rec(
            "Process 2306 (kscreen_backend) of user 1000 dumped core.",
            "systemd-coredump@0-22095-0.service"))[0]
        self.assertTrue(err.owned)
        self.assertFalse(err.platform)

    def test_coredump_notice_stays_base(self) -> None:
        # 同一个单元的非崩溃提示（未编入 libelf）不是缺陷，仍按基底计数
        err = osys.journal_errs(rec(
            "elfutils disabled, parsing ELF objects not supported",
            "systemd-coredump@0-22095-0.service"))[0]
        self.assertFalse(err.owned)
        self.assertEqual(err.unit, "coredump")

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


class BudgetTest(unittest.TestCase):
    def test_group_counts_by_class(self) -> None:
        groups = osys.parse_budget(BUDGET, FP)
        self.assertEqual(groups["real"], osys.Budget("real", 1, 1, True))
        self.assertEqual(groups["qemu"], osys.Budget("qemu", 0, 1, True))

    def test_platform_entries_follow_fingerprint(self) -> None:
        # 同一块盘换到另一台机器：旧主板的平台条目一条也不继承
        groups = osys.parse_budget(BUDGET, "000000000000")
        self.assertEqual(groups["real"].platform, 0)
        self.assertEqual(groups["real"].base, 1)

    def test_missing_group_is_negative(self) -> None:
        # 「读不到预算就放行」等于把棘轮拆掉，缺组必须判负
        bud = osys.budget_for(osys.parse_budget(BUDGET, FP), "other")
        self.assertFalse(bud.read)
        self.assertEqual(bud.cls, "other")

    def test_owned_entry_voids_group(self) -> None:
        # owned 层的额度恒为 0，文件里出现即整组作废
        text = f"class=real\nowned 2026-11-10 想给自有缺陷留额度\n"
        groups = osys.parse_budget(text, FP)
        self.assertFalse(groups["real"].read)

    def test_short_entry_voids_group(self) -> None:
        # 条目必须带归因证据（指纹/日期/摘要），缺字段即认不出这条债
        text = "class=real\nbase 2026-11-06\n"
        groups = osys.parse_budget(text, FP)
        self.assertFalse(groups["real"].read)

    def test_comments_and_blank_lines_ignored(self) -> None:
        text = "# 说明\n\nclass=qemu\n# 段内注释\nbase 2026-11-06 x y\n"
        groups = osys.parse_budget(text, FP)
        self.assertEqual(groups["qemu"], osys.Budget("qemu", 0, 1, True))

    def test_machine_fp_is_stable_digest(self) -> None:
        dmi = {"product_name": "83JK", "board_name": "LNVNB161216",
               "bios_version": "QLCN24WW"}
        self.assertRegex(osys.machine_fp(dmi), r"^[0-9a-f]{12}$")
        self.assertEqual(osys.machine_fp(dmi), osys.machine_fp(dict(dmi)))
        self.assertNotEqual(
            osys.machine_fp(dmi),
            osys.machine_fp({**dmi, "bios_version": "QLCN99WW"}),
        )

    def test_machine_class_detects_virtual(self) -> None:
        self.assertEqual(osys.machine_class(
            {"product_name": "Standard PC (Q35 + ICH9, 2009)"}), "qemu")
        self.assertEqual(osys.machine_class(
            {"product_name": "83JK", "board_name": "LNVNB161216"}), "real")
        self.assertEqual(osys.machine_class({}), "real")

    def test_dmi_live_follows_view(self) -> None:
        # --root 指向只读视图时机器身份也取自那里，判据不读宿主的主板
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / osys.DMI_REL
            root.mkdir(parents=True)
            (root / "product_name").write_text("83JK\n", encoding="utf-8")
            values = osys.dmi_live(root)
        self.assertEqual(values["product_name"], "83JK")
        self.assertEqual(values["bios_version"], "")


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

    def test_excluded_unit_is_not_evidence(self) -> None:
        # 判定者不把自己的结论当证据：上报单元的失败由它所承载的那轮 TAP
        # 判定驱动，再算进证据就形成自指，同一次开机的后续每一轮都会读回
        # 这条旧结论，真实根因因此被盖住（285H 实测）。
        found = osys.collect(
            "airymax-smoke-report.service\n", JOB_FAIL, RPM, MANIFEST,
            exclude=("airymax-smoke-report.service",),
        )
        self.assertEqual(found.state, osys.STATE_OK)
        self.assertEqual(found.units, ())
        self.assertEqual(found.errors, ())

    def test_exclusion_keeps_other_failures(self) -> None:
        # 排除只对点名单元生效，同一次开机里的其他失败单元照常判负
        found = osys.collect(
            "airymax-smoke-report.service\nairymax-data.mount\n", "",
            RPM, MANIFEST, exclude=("airymax-smoke-report.service",),
        )
        self.assertEqual(found.units, ("airymax-data.mount",))
        self.assertEqual(found.state, osys.STATE_FAILED)

    def test_counts_split_four_layers(self) -> None:
        text = "\n".join((BASE_ERR, OWNED, PLAT, "[ 1.0] bare line"))
        found = osys.collect("", text, RPM, MANIFEST)
        self.assertEqual(
            (found.owned_n, found.platform_n, found.base_n,
             found.unknown_n), (1, 1, 1, 1),
        )

    def test_base_within_budget(self) -> None:
        found = osys.collect("", BASE_ERR, RPM, MANIFEST,
                             osys.Budget(base=1))
        self.assertEqual(found.logs_state, osys.STATE_OK)
        self.assertEqual(found.state, osys.STATE_OK)

    def test_base_over_budget(self) -> None:
        found = osys.collect("", BASE_ERR, RPM, MANIFEST)
        self.assertEqual(found.logs_state, osys.STATE_ERRORS)

    def test_platform_has_its_own_budget(self) -> None:
        # 平台层与基底层各记各的账：基底额度不能挪给平台噪音，反之亦然
        text = "\n".join((PLAT, PLAT_TZ))
        found = osys.collect("", text, RPM, MANIFEST,
                             osys.Budget(base=9))
        self.assertEqual(found.logs_state, osys.STATE_ERRORS)
        found = osys.collect("", BASE_ERR, RPM, MANIFEST,
                             osys.Budget(platform=9))
        self.assertEqual(found.logs_state, osys.STATE_ERRORS)

    def test_owned_ignores_budget(self) -> None:
        # 自有单元的 err 是本产品缺陷，预算再宽也不能放过
        found = osys.collect("", OWNED, RPM, MANIFEST,
                             osys.Budget(platform=9, base=99))
        self.assertEqual(found.logs_state, osys.STATE_ERRORS)

    def test_unknown_layer_ignores_budget(self) -> None:
        found = osys.collect("", JOURNAL, RPM, MANIFEST,
                             osys.Budget(platform=9, base=99))
        self.assertEqual(found.logs_state, osys.STATE_ERRORS)

    def test_missing_group_fails_clean_journal(self) -> None:
        # 日志干净但预算缺组同样判负：缺证据的预算不能当成放行
        bud = osys.budget_for(osys.parse_budget(BUDGET, FP), "other")
        found = osys.collect("", "", RPM, MANIFEST, bud)
        self.assertEqual(found.logs_state, osys.STATE_ERRORS)
        self.assertEqual(found.state, osys.STATE_FAILED)

    def test_missing_manifest_is_unknown(self) -> None:
        found = osys.collect("", "", RPM, None)
        self.assertEqual(found.packs_state, osys.STATE_UNKNOWN)
        self.assertEqual(found.state, osys.STATE_UNKNOWN)

    def test_missing_journal_is_unknown(self) -> None:
        found = osys.collect("", None, RPM, MANIFEST)
        self.assertEqual(found.logs_state, osys.STATE_UNKNOWN)

    def test_dump_carries_layers(self) -> None:
        found = osys.collect("", BASE_ERR, RPM, MANIFEST,
                             osys.Budget(base=3))
        logs = found.dump()["logs"]
        self.assertEqual(logs["budget"],
                         {"platform": 0, "base": 3, "read": True})
        self.assertEqual(logs["base"], 1)
        self.assertEqual(logs["platform"], 0)
        self.assertEqual(logs["class"], "real")
        self.assertEqual(logs["errors"], [json.loads(BASE_ERR)["MESSAGE"]])


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

    def budget(self, body: str) -> str:
        path = Path(tempfile.mkdtemp(), "err-budget")
        path.write_text(body, encoding="utf-8")
        return str(path)

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

    def test_exclude_flag_drops_named_unit(self) -> None:
        units, journal, rpm, manifest = self.files(
            "airymax-smoke-report.service\n", JOB_FAIL, RPM, MANIFEST)
        argv = ("system", "--units", units, "--journal", journal,
                "--rpm", rpm, "--manifest", manifest)
        self.assertEqual(self.run_cli(*argv)[0], 1)
        code, out = self.run_cli(
            *argv, "--exclude", "airymax-smoke-report.service")
        self.assertEqual(code, 0, out)
        self.assertIn("units=0", out)

    def test_budget_file_lifts_base_only(self) -> None:
        units, journal, rpm, manifest = self.files("", BASE_ERR, RPM, MANIFEST)
        argv = ("system", "--units", units, "--journal", journal,
                "--rpm", rpm, "--manifest", manifest,
                "--class", "real", "--machine-fp", FP)
        self.assertEqual(self.run_cli(*argv)[0], 1)
        code, out = self.run_cli(*argv, "--budget-file", self.budget(BUDGET))
        self.assertEqual(code, 0, out)
        self.assertIn("base=1", out)
        self.assertIn("budget=platform:1/base:1", out)

    def test_platform_marked_and_gated(self) -> None:
        # 平台层在文本里带层标；额度由条目数决定，缺额度即判失败
        units, journal, rpm, manifest = self.files("", PLAT, RPM, MANIFEST)
        argv = ("system", "--units", units, "--journal", journal,
                "--rpm", rpm, "--manifest", manifest,
                "--class", "real", "--machine-fp", FP)
        code, out = self.run_cli(*argv, "--budget-file", self.budget(BUDGET))
        self.assertEqual(code, 0, out)
        self.assertIn("platform=1", out)
        self.assertIn("[platform]", out)
        code, out = self.run_cli(
            *argv, "--budget-file",
            self.budget(f"class=real\nbase 2026-11-06 x y\n"))
        self.assertEqual(code, 1, out)

    def test_missing_group_reports_and_fails(self) -> None:
        units, journal, rpm, manifest = self.files("", "", RPM, MANIFEST)
        code, out = self.run_cli(
            "system", "--units", units, "--journal", journal,
            "--rpm", rpm, "--manifest", manifest,
            "--class", "real", "--machine-fp", FP,
            "--budget-file", self.budget("class=qemu\nbase 2026-11-06 x y\n"),
        )
        self.assertEqual(code, 1, out)
        self.assertIn("budget=missing:real", out)

    def test_owned_marked_in_text(self) -> None:
        units, journal, rpm, manifest = self.files("", OWNED, RPM, MANIFEST)
        code, out = self.run_cli(
            "system", "--units", units, "--journal", journal,
            "--rpm", rpm, "--manifest", manifest,
            "--class", "real", "--machine-fp", FP,
            "--budget-file", self.budget(BUDGET),
        )
        self.assertEqual(code, 1)
        self.assertIn("owned=1", out)
        self.assertIn("[owned]", out)

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
